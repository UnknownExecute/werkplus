"""Mailversand von Belegen (PDF-Anhang) per SMTP — stdlib ``smtplib``/``email``.

Konfiguration über ``settings.smtp_*`` (siehe .env.example). Ohne SMTP-Host ist
der Versand aus; die UI zeigt dann einen Hinweis statt eines Sendeknopfs.

``sende_beleg`` rendert das PDF, verschickt es, protokolliert eine E-Mail-
Aktivität beim Kunden und bucht einen Entwurf (Status „gedruckt/versendet") —
ein per Mail verschickter Beleg ist nach außen gegangen und damit unveränderlich,
genau wie ein gedruckter.

WICHTIG: In diesem Modul darf nie das SMTP-Passwort geloggt werden.
"""

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from sqlmodel import Session

from app.config import settings
from app.models.adresse import Adresse
from app.models.aktivitaet import Aktivitaet, AktivitaetTyp
from app.models.beleg import Beleg, BelegStatus
from app.services import beleg_service, firma_service, pdf_service

logger = logging.getLogger(__name__)

TIMEOUT_SEKUNDEN = 20


class MailNichtKonfiguriert(Exception):
    """Kein SMTP-Host hinterlegt."""


class MailFehler(Exception):
    """Der Versand ist fehlgeschlagen (Verbindung, Login, Ablehnung)."""


def ist_konfiguriert() -> bool:
    return bool(settings.smtp_host.strip())


def absender(session: Session) -> str:
    """Absenderadresse: SMTP-Absender, sonst Firmen-E-Mail aus den Stammdaten."""
    if settings.smtp_absender.strip():
        return settings.smtp_absender.strip()
    return firma_service.lade_firma(session).email


def _empfaenger_liste(text: str) -> list[str]:
    """„a@x.at; b@y.at, c@z.at" → Liste, leere Einträge weg."""
    roh = text.replace(";", ",").split(",")
    return [t.strip() for t in roh if t.strip()]


def _smtp_factory():
    """Verbindung nach ``settings.smtp_sicherheit`` aufbauen (in Tests ersetzbar)."""
    host, port = settings.smtp_host.strip(), settings.smtp_port
    if settings.smtp_sicherheit == "ssl":
        return smtplib.SMTP_SSL(
            host, port, timeout=TIMEOUT_SEKUNDEN, context=ssl.create_default_context()
        )
    verbindung = smtplib.SMTP(host, port, timeout=TIMEOUT_SEKUNDEN)
    if settings.smtp_sicherheit != "keine":
        verbindung.starttls(context=ssl.create_default_context())
    return verbindung


def sende_mail(  # noqa: PLR0913
    von: str,
    an: list[str],
    betreff: str,
    text: str,
    anhang: tuple[str, bytes, str] | None = None,
    cc: list[str] | None = None,
    absendername: str = "",
) -> None:
    """Verschickt eine Text-Mail mit optionalem Anhang ``(dateiname, bytes, mimetype)``."""
    if not ist_konfiguriert():
        raise MailNichtKonfiguriert("Kein SMTP-Server konfiguriert (WERKPLUS_SMTP_HOST)")
    if not an:
        raise MailFehler("Kein Empfänger angegeben")
    if not von:
        raise MailFehler("Keine Absenderadresse (WERKPLUS_SMTP_ABSENDER oder Firmen-E-Mail)")

    nachricht = EmailMessage()
    nachricht["From"] = formataddr((absendername, von)) if absendername else von
    nachricht["To"] = ", ".join(an)
    if cc:
        nachricht["Cc"] = ", ".join(cc)
    nachricht["Subject"] = betreff
    nachricht.set_content(text)
    if anhang is not None:
        dateiname, daten, mimetype = anhang
        haupt, _, unter = mimetype.partition("/")
        nachricht.add_attachment(daten, maintype=haupt, subtype=unter or "octet-stream",
                                 filename=dateiname)

    try:
        with _smtp_factory() as verbindung:
            if settings.smtp_benutzer:
                verbindung.login(settings.smtp_benutzer, settings.smtp_passwort)
            verbindung.send_message(nachricht, from_addr=von, to_addrs=list(an) + list(cc or []))
    except smtplib.SMTPAuthenticationError as exc:
        logger.warning("SMTP-Login abgelehnt (Benutzer %s)", settings.smtp_benutzer)
        raise MailFehler(
            "Der Mailserver lehnt Benutzer/Passwort ab. Bitte WERKPLUS_SMTP_BENUTZER und "
            "WERKPLUS_SMTP_PASSWORT in der .env prüfen."
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        logger.warning("Mailversand fehlgeschlagen: %s", exc)
        raise MailFehler(f"Mailversand fehlgeschlagen: {exc}") from exc


def vorschlag(session: Session, beleg: Beleg, belegart_label: str) -> dict:
    """Vorbelegung für den Mail-Dialog: Empfänger, Betreff, Text."""
    adresse = session.get(Adresse, beleg.adresse_id)
    firma = firma_service.lade_firma(session)
    empfaenger = adresse.email if adresse else ""
    anrede = "Sehr geehrte Damen und Herren,"
    if adresse and adresse.briefanrede:
        anrede = adresse.briefanrede
    betreff = f"{belegart_label} {beleg.belegnr}"
    if beleg.betreff:
        betreff += f" – {beleg.betreff}"
    text = (
        f"{anrede}\n\n"
        f"anbei erhalten Sie {belegart_label} {beleg.belegnr} als PDF.\n\n"
        f"Mit freundlichen Grüßen\n{firma.name}\n{firma.telefon}\n{firma.email}"
    )
    return {"an": empfaenger, "betreff": betreff, "text": text}


def sende_beleg(  # noqa: PLR0913
    session: Session,
    beleg: Beleg,
    an: str,
    betreff: str,
    text: str,
    belegart_label: str,
    benutzer_id: int | None = None,
    cc: str = "",
) -> list[str]:
    """PDF rendern, verschicken, Aktivität protokollieren, Entwurf buchen.

    Liefert die Empfängerliste. Reihenfolge bewusst: erst senden, dann buchen —
    schlägt der Versand fehl, bleibt der Beleg unverändert.
    """
    if beleg.ist_muster:
        raise MailFehler("Muster werden nicht versendet — zuerst einen Beleg daraus erzeugen")
    empfaenger = _empfaenger_liste(an)
    kopie = _empfaenger_liste(cc)
    if not empfaenger:
        raise MailFehler("Bitte mindestens einen Empfänger angeben")
    firma = firma_service.lade_firma(session)
    pdf = pdf_service.render_beleg_pdf(session, beleg)
    dateiname = f"{belegart_label}_{beleg.belegnr}.pdf".replace(" ", "_")
    sende_mail(
        von=absender(session),
        an=empfaenger,
        cc=kopie,
        betreff=betreff.strip() or f"{belegart_label} {beleg.belegnr}",
        text=text,
        anhang=(dateiname, pdf, "application/pdf"),
        absendername=firma.name,
    )
    session.add(
        Aktivitaet(
            adresse_id=beleg.adresse_id,
            typ=AktivitaetTyp.EMAIL,
            betreff=f"{belegart_label} {beleg.belegnr} per Mail versendet",
            text=f"an {', '.join(empfaenger)}" + (f", Kopie {', '.join(kopie)}" if kopie else ""),
            benutzer_id=benutzer_id,
            beleg_id=beleg.id,
        )
    )
    if beleg.status == BelegStatus.ENTWURF:
        beleg_service.setze_status(session, beleg, BelegStatus.GEDRUCKT, benutzer_id=benutzer_id)
    session.flush()
    return empfaenger

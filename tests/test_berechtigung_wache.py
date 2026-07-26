"""Tests für die granulare Durchsetzung der Berechtigungswache.

Geprüft wird ``app.services.berechtigung.bereich_wache`` als Router-Dependency
über echte HTTP-Requests — also genau die Schicht, die jeden Bereichs-Router
abriegelt:

* **Methodenabhängige Stufe**: GET verlangt ``Zugriff.ANSEHEN``, schreibende
  Methoden (POST/PUT/PATCH/DELETE) verlangen ``Zugriff.BEARBEITEN``.
* **``Zugriff.KEIN``** sperrt den Bereich vollständig — auch lesend.
* **ODER-Verknüpfung** mehrerer Bereiche (Belege-Router: verkauf/einkauf).
* **Fallback** ohne Rollenprofil in der DB (``_fallback_profil``):
  Rollenname ``admin`` ⇒ Vollzugriff, sonst operatives Sachbearbeiter-Profil.
* **``ist_admin``** schlägt alle einzelnen ``recht_*``-Felder.
* **Zeitpunkt der Wirkung**: die Wache liest bei JEDEM Request frisch aus der DB,
  die Menü-/Template-Sichtbarkeit stammt dagegen aus der beim Login gefüllten
  Session (siehe ``rechte_als_dict``/``darf_bereich``) und ist damit bis zum
  Re-Login veraltet. Das ist eine bewusste Eigenschaft, kein Fehler: die
  *Durchsetzung* ist immer aktuell, nur die *Anzeige* hinkt nachher.

Ergänzt die vorhandenen Tests: ``test_bereichsschutz.py`` (objektbezogene
Verkauf/Einkauf-Feinprüfung), ``test_berechtigung_ui.py`` (Kalkulations-
Ausblendung), ``test_admin_schutz.py``/``test_benutzer.py``/``test_firma.py``
(admin-gated Routen).

Hinweis zu den Statuscodes: 403 rendert ``templates/403.html`` (Status bleibt
403), ein 401 wird vom Handler in app/main.py in einen 302 auf ``/login``
umgeschrieben — deshalb ``follow_redirects=False``, wo das relevant ist.
"""

import pytest
from sqlmodel import Session, select

from app.models import Adresse, AdressTyp, Beleg, Belegart, Rolle, Rollenprofil, Zugriff
from app.models.benutzer import Benutzer
from app.services.auth_service import hash_passwort

FORBIDDEN = 403
OK = 200
REDIRECT = 302

# Bereich → (Listen-Route für GET, schreibende Route für POST)
# Alle Pfade sind echte Router-Pfade (app/routers/*.py), deren Router genau mit
# ``bereich_wache(<bereich>)`` geschützt ist.
BEREICH_ROUTEN: list[tuple[str, str, str]] = [
    ("adressen", "/adressen", "/adressen/neu"),
    ("artikel", "/artikel", "/artikel/neu"),
    ("lager", "/lager/bewegungen", "/lager/korrektur"),
    ("anlagen", "/anlagen", "/anlagen/neu"),
]

# Nur-lesende Routen je Bereich — auch die beiden Router, die sich einen Bereich
# teilen (artikel ⇒ /artikel und /textbausteine).
NUR_LESEN_ROUTEN: list[tuple[str, str]] = [
    ("adressen", "/adressen"),
    ("artikel", "/artikel"),
    ("artikel", "/textbausteine"),
    ("lager", "/lager/bewegungen"),
    ("anlagen", "/anlagen"),
    ("benutzer", "/benutzer"),
]


# --- Hilfen (gleiches Muster wie tests/test_bereichsschutz.py) ----------------


def _benutzer_anlegen(engine, *, login: str, rolle: str) -> None:
    """Legt einen aktiven Benutzer mit dem angegebenen Rollennamen an."""
    with Session(engine) as session:
        session.add(
            Benutzer(
                login=login,
                name=login.title(),
                passwort_hash=hash_passwort("geheim"),
                rolle=rolle,
                aktiv=True,
            )
        )
        session.commit()


def _anmelden(client, login: str) -> None:
    r = client.post(
        "/login", data={"login": login, "passwort": "geheim"}, follow_redirects=False
    )
    assert r.status_code == REDIRECT, f"Login für {login!r} fehlgeschlagen: {r.status_code}"


def _login_mit_rechten(engine, client, *, login: str, **rechte) -> str:
    """Rollenprofil mit den Bereichsrechten anlegen, Benutzer anlegen, anmelden.

    Liefert den Namen des Rollenprofils zurück (für späteres Ändern in der DB).
    """
    rollenname = f"rolle_{login}"
    with Session(engine) as session:
        session.add(Rollenprofil(name=rollenname, beschreibung="Testrolle", **rechte))
        session.commit()
    _benutzer_anlegen(engine, login=login, rolle=rollenname)
    _anmelden(client, login)
    return rollenname


def _login_ohne_profil(engine, client, *, login: str, rolle: str) -> None:
    """Benutzer mit einem Rollennamen anmelden, für den KEIN Rollenprofil existiert."""
    _benutzer_anlegen(engine, login=login, rolle=rolle)
    with Session(engine) as session:
        vorhanden = session.exec(
            select(Rollenprofil).where(Rollenprofil.name == rolle)
        ).first()
        assert vorhanden is None, f"Für {rolle!r} darf kein Rollenprofil existieren"
    _anmelden(client, login)


def _recht_setzen(engine, rollenname: str, bereich: str, stufe: Zugriff) -> None:
    """Ändert ein Bereichsrecht des Rollenprofils direkt in der DB (laufende Sitzung)."""
    with Session(engine) as session:
        profil = session.exec(
            select(Rollenprofil).where(Rollenprofil.name == rollenname)
        ).one()
        setattr(profil, f"recht_{bereich}", stufe)
        session.add(profil)
        session.commit()


# --- 1) Methodenabhängige Stufe: GET ⇒ ansehen, POST ⇒ bearbeiten ------------


@pytest.mark.parametrize(("bereich", "get_pfad", "post_pfad"), BEREICH_ROUTEN)
def test_ansehen_erlaubt_lesenden_zugriff(engine, client, bereich, get_pfad, post_pfad):
    """``recht_<bereich> = ANSEHEN`` genügt für GET (Stufe "ansehen")."""
    _login_mit_rechten(
        engine, client, login=f"leser_{bereich}", **{f"recht_{bereich}": Zugriff.ANSEHEN}
    )
    r = client.get(get_pfad)
    assert r.status_code == OK, (
        f"GET {get_pfad} muss mit recht_{bereich}=ansehen erlaubt sein, "
        f"war aber {r.status_code}"
    )


@pytest.mark.parametrize(("bereich", "get_pfad", "post_pfad"), BEREICH_ROUTEN)
def test_ansehen_verbietet_schreibenden_zugriff(engine, client, bereich, get_pfad, post_pfad):
    """``ANSEHEN`` reicht NICHT für POST — schreibende Methoden verlangen BEARBEITEN."""
    _login_mit_rechten(
        engine, client, login=f"leser_{bereich}", **{f"recht_{bereich}": Zugriff.ANSEHEN}
    )
    r = client.post(post_pfad, data={}, follow_redirects=False)
    assert r.status_code == FORBIDDEN, (
        f"POST {post_pfad} muss mit recht_{bereich}=ansehen 403 ergeben, "
        f"war aber {r.status_code}"
    )


@pytest.mark.parametrize(("bereich", "get_pfad", "post_pfad"), BEREICH_ROUTEN)
def test_bearbeiten_passiert_die_wache(engine, client, bereich, get_pfad, post_pfad):
    """Gegenprobe: mit ``BEARBEITEN`` kommt derselbe POST an der Wache vorbei.

    Der Statuscode danach ist bewusst offen (200 mit Formularfehler oder 422 wegen
    fehlender Pflichtfelder) — entscheidend ist nur, dass es NICHT 403 ist.
    """
    _login_mit_rechten(
        engine,
        client,
        login=f"bearbeiter_{bereich}",
        **{f"recht_{bereich}": Zugriff.BEARBEITEN},
    )
    r = client.post(post_pfad, data={}, follow_redirects=False)
    assert r.status_code != FORBIDDEN, (
        f"POST {post_pfad} darf mit recht_{bereich}=bearbeiten nicht 403 ergeben"
    )


def test_delete_verlangt_bearbeiten(engine, client):
    """Auch DELETE zählt als schreibend (``_SCHREIB_METHODEN``).

    Die Wache greift vor dem Handler — deshalb genügen erfundene IDs, es wird
    nichts gelöscht.
    """
    _login_mit_rechten(engine, client, login="leser_delete", recht_verkauf=Zugriff.ANSEHEN)
    r = client.delete("/belege/detail/999999/positionen/999999")
    assert r.status_code == FORBIDDEN, (
        f"DELETE muss mit recht_verkauf=ansehen 403 ergeben, war aber {r.status_code}"
    )


# --- 2) Zugriff.KEIN sperrt den Bereich komplett (auch lesend) ---------------


@pytest.mark.parametrize(("bereich", "get_pfad"), NUR_LESEN_ROUTEN)
def test_kein_sperrt_auch_lesenden_zugriff(engine, client, bereich, get_pfad):
    """``recht_<bereich> = KEIN`` ⇒ selbst GET ergibt 403."""
    # Ein anderer Bereich ist bewusst offen, damit der Login/das Menü funktioniert
    # und der 403 nachweislich vom geprüften Bereich kommt.
    rechte = {f"recht_{b}": Zugriff.KEIN for b, _ in NUR_LESEN_ROUTEN}
    rechte["recht_verkauf"] = Zugriff.BEARBEITEN
    _login_mit_rechten(engine, client, login=f"gesperrt_{bereich}", **rechte)

    r = client.get(get_pfad, follow_redirects=False)
    assert r.status_code == FORBIDDEN, (
        f"GET {get_pfad} muss mit recht_{bereich}=kein 403 ergeben, war aber {r.status_code}"
    )


def test_kein_ist_der_standard_eines_leeren_profils(engine, client):
    """Ein Rollenprofil ohne gesetzte Rechte sperrt alles (Feld-Default ``KEIN``)."""
    _login_mit_rechten(engine, client, login="nullrechte")
    for _, get_pfad in NUR_LESEN_ROUTEN:
        r = client.get(get_pfad, follow_redirects=False)
        assert r.status_code == FORBIDDEN, f"GET {get_pfad} müsste 403 sein, war {r.status_code}"


# --- 3) ODER-Verknüpfung mehrerer Bereiche (Belege-Router) -------------------


def test_oder_wache_ein_bereich_genuegt_verkauf(engine, client):
    """``bereich_wache("verkauf", "einkauf")``: nur ``verkauf`` passiert die Wache."""
    _login_mit_rechten(engine, client, login="nurverkauf_oder", recht_verkauf=Zugriff.ANSEHEN)
    r = client.get("/belege/angebot")
    assert r.status_code == OK, f"Angebotsliste muss erlaubt sein, war {r.status_code}"


def test_oder_wache_ein_bereich_genuegt_einkauf(engine, client):
    """Gegenrichtung: nur ``einkauf`` passiert dieselbe Wache."""
    _login_mit_rechten(engine, client, login="nureinkauf_oder", recht_einkauf=Zugriff.ANSEHEN)
    r = client.get("/belege/bestellung")
    assert r.status_code == OK, f"Bestellliste muss erlaubt sein, war {r.status_code}"


def test_oder_wache_ohne_beide_bereiche_gesperrt(engine, client):
    """Fehlen BEIDE Bereiche, greift die ODER-Wache und sperrt den Belege-Router."""
    _login_mit_rechten(engine, client, login="nurstammdaten", recht_adressen=Zugriff.BEARBEITEN)
    r = client.get("/belege/angebot", follow_redirects=False)
    assert r.status_code == FORBIDDEN, (
        f"Belege-Router muss ohne verkauf/einkauf 403 ergeben, war {r.status_code}"
    )


def test_oder_wache_schreibstufe_wird_bereichsuebergreifend_erfuellt(engine, client):
    """Die Wache prüft die Schreibstufe pro Bereich, verknüpft aber mit ODER.

    ``verkauf=ansehen`` + ``einkauf=bearbeiten`` lässt einen POST auf dem
    Belege-Router passieren, weil EIN Bereich die Stufe erfüllt. Die
    belegartgenaue Prüfung ist dann Sache von ``_pruefe_belegart_recht``
    (siehe tests/test_bereichsschutz.py) — hier geht es nur um die Wache.
    """
    _login_mit_rechten(
        engine,
        client,
        login="mischrechte",
        recht_verkauf=Zugriff.ANSEHEN,
        recht_einkauf=Zugriff.BEARBEITEN,
    )
    # Erfundene Beleg-ID: die Wache entscheidet vor dem Handler.
    r = client.post("/belege/detail/999999/kopf", data={}, follow_redirects=False)
    assert r.status_code != FORBIDDEN, (
        "Die ODER-Wache muss den POST durchlassen, wenn einkauf=bearbeiten gesetzt ist"
    )


# --- 3b) Generische Belegart-Routen prüfen den konkreten Bereich -------------
# Ursprünglich als Lücke gefunden (nur die Detail-Routen prüften über
# _lade_beleg(request=...)); behoben durch _belegart_mit_recht in
# app/routers/belege.py, das Liste, Suche und Neuanlage mitschützt.


def _kunde_und_lieferant(engine) -> int:
    with Session(engine) as session:
        adresse = Adresse(typ=AdressTyp.BEIDE, firma="Zwitter GmbH", ort="Linz")
        session.add(adresse)
        session.commit()
        return adresse.id


def test_belegartliste_prueft_den_konkreten_bereich(engine, client):
    """Nur-Verkauf-Recht darf die Einkaufsbelegliste nicht lesen können."""
    _login_mit_rechten(engine, client, login="nurverkauf_liste", recht_verkauf=Zugriff.BEARBEITEN)
    r = client.get("/belege/bestellung", follow_redirects=False)
    assert r.status_code == FORBIDDEN


def test_belegart_neu_prueft_den_konkreten_bereich(engine, client):
    """Nur-Verkauf-Recht darf keinen Einkaufsbeleg (Bestellung) anlegen können."""
    adresse_id = _kunde_und_lieferant(engine)
    _login_mit_rechten(engine, client, login="nurverkauf_neu", recht_verkauf=Zugriff.BEARBEITEN)
    r = client.post(
        "/belege/bestellung/neu", data={"adresse_id": adresse_id}, follow_redirects=False
    )
    assert r.status_code == FORBIDDEN


def test_belegart_neu_legt_keinen_fremdbeleg_an(engine, client):
    """Wirkungsnachweis: der abgewiesene POST hinterlässt auch keinen Beleg.

    Ein 403 allein genügt als Beleg nicht — entscheidend ist, dass nichts in der
    Datenbank landet. Dieser Test hielt ursprünglich die Lücke als Ist-Zustand
    fest (Beleg wurde trotz fehlendem Einkaufsrecht angelegt).
    """
    adresse_id = _kunde_und_lieferant(engine)
    _login_mit_rechten(engine, client, login="nurverkauf_effekt", recht_verkauf=Zugriff.BEARBEITEN)

    r = client.post(
        "/belege/bestellung/neu", data={"adresse_id": adresse_id}, follow_redirects=False
    )
    assert r.status_code == FORBIDDEN
    with Session(engine) as session:
        bestellungen = session.exec(
            select(Beleg).where(Beleg.belegart == Belegart.BESTELLUNG)
        ).all()
    assert len(bestellungen) == 0, "Trotz 403 wurde ein Einkaufsbeleg angelegt"


@pytest.mark.parametrize(
    "pfad",
    [
        "/belege/bestellung",
        "/belege/bestellung/suche",
        "/belege/bestellung/neu",
        "/belege/ek_rechnung",
        "/belege/ek_lieferschein",
    ],
)
def test_alle_generischen_einkaufsrouten_gesperrt(engine, client, pfad):
    """Liste, HTMX-Suche und Neuanlage-Formular müssen alle den Bereich prüfen.

    Die HTMX-Suche wäre sonst ein bequemer Umweg: sie liefert dieselben Zeilen
    wie die Liste, nur als Partial.
    """
    # Login-Name deterministisch aus dem Pfad (kein hash() — über Läufe instabil)
    login = "nv_" + pfad.strip("/").replace("/", "_")
    _login_mit_rechten(engine, client, login=login, recht_verkauf=Zugriff.BEARBEITEN)
    r = client.get(pfad, follow_redirects=False)
    assert r.status_code == FORBIDDEN, f"{pfad} war trotz fehlendem Einkaufsrecht erreichbar"


def test_belegart_recht_wird_frisch_aus_der_db_gelesen(engine, client):
    """Rechteentzug wirkt in der Durchsetzung sofort — nicht erst nach Re-Login.

    Die Feinprüfung las früher aus der Session (``darf_bereich``) und hinkte
    einem Entzug damit bis zum nächsten Login nach. Sie liest jetzt wie die
    Router-Wache frisch aus der Datenbank.
    """
    rollenname = _login_mit_rechten(
        engine, client, login="entzug_einkauf",
        recht_verkauf=Zugriff.BEARBEITEN, recht_einkauf=Zugriff.BEARBEITEN,
    )
    assert client.get("/belege/bestellung", follow_redirects=False).status_code == OK

    # Einkaufsrecht entziehen, Sitzung bewusst NICHT erneuern
    _recht_setzen(engine, rollenname, "einkauf", Zugriff.KEIN)

    r = client.get("/belege/bestellung", follow_redirects=False)
    assert r.status_code == FORBIDDEN, (
        "Entzogenes Einkaufsrecht wirkte erst nach Re-Login — Durchsetzung liest veraltete Session"
    )


# --- 4) Fallback ohne Rollenprofil in der DB ---------------------------------


@pytest.mark.parametrize(
    "get_pfad", ["/adressen", "/artikel", "/lager/bewegungen", "/anlagen", "/benutzer"]
)
def test_fallback_admin_rollenname_hat_vollzugriff(engine, client, get_pfad):
    """Kein Rollenprofil + Rollenname ``admin`` ⇒ Vollzugriff (``ist_admin``)."""
    _login_ohne_profil(engine, client, login="chefin", rolle=Rolle.ADMIN)
    r = client.get(get_pfad, follow_redirects=False)
    assert r.status_code == OK, (
        f"Fallback-Admin muss {get_pfad} sehen dürfen, war aber {r.status_code}"
    )


@pytest.mark.parametrize(("bereich", "get_pfad", "post_pfad"), BEREICH_ROUTEN)
def test_fallback_operativ_darf_operative_bereiche_bearbeiten(
    engine, client, bereich, get_pfad, post_pfad
):
    """Kein Rollenprofil + beliebiger anderer Rollenname ⇒ operatives Profil.

    Alle operativen Bereiche sind darin ``BEARBEITEN``, also passieren GET UND
    POST die Wache.
    """
    _login_ohne_profil(engine, client, login=f"monteur_{bereich}", rolle="monteur")
    assert client.get(get_pfad).status_code == OK, f"GET {get_pfad} müsste erlaubt sein"
    r = client.post(post_pfad, data={}, follow_redirects=False)
    assert r.status_code != FORBIDDEN, f"POST {post_pfad} dürfte nicht 403 sein"


@pytest.mark.parametrize("get_pfad", ["/benutzer", "/einstellungen"])
def test_fallback_operativ_sperrt_systembereiche(engine, client, get_pfad):
    """Das operative Fallback-Profil hat einstellungen/benutzer auf ``KEIN``."""
    _login_ohne_profil(engine, client, login="monteur_system", rolle="monteur")
    r = client.get(get_pfad, follow_redirects=False)
    assert r.status_code == FORBIDDEN, (
        f"GET {get_pfad} muss für das operative Fallback-Profil 403 ergeben, "
        f"war aber {r.status_code}"
    )


def test_fallback_leerer_rollenname_ist_operativ_nicht_admin(engine, client):
    """Randfall: leerer Rollenname fällt auf ``sachbearbeiter`` zurück, nicht auf admin."""
    _login_ohne_profil(engine, client, login="ohnerolle", rolle="")
    assert client.get("/adressen").status_code == OK
    assert client.get("/benutzer", follow_redirects=False).status_code == FORBIDDEN


def test_fallback_greift_nicht_wenn_profil_existiert(engine, client):
    """Gegenprobe: existiert ein Profil, gilt AUSSCHLIESSLICH dieses.

    Ein Rollenprofil namens ``admin`` ohne ``ist_admin`` darf NICHT über den
    Rollennamen zum Vollzugriff führen.
    """
    _login_mit_rechten(engine, client, login="scheinadmin", recht_adressen=Zugriff.ANSEHEN)
    with Session(engine) as session:
        profil = session.exec(
            select(Rollenprofil).where(Rollenprofil.name == "rolle_scheinadmin")
        ).one()
        profil.name = Rolle.ADMIN
        session.add(profil)
        session.commit()
    with Session(engine) as session:
        benutzer = session.exec(select(Benutzer).where(Benutzer.login == "scheinadmin")).one()
        benutzer.rolle = Rolle.ADMIN
        session.add(benutzer)
        session.commit()

    assert client.get("/adressen").status_code == OK
    assert client.get("/benutzer", follow_redirects=False).status_code == FORBIDDEN


# --- 5) ist_admin schlägt alle einzelnen Bereichsrechte ----------------------


@pytest.mark.parametrize(("bereich", "get_pfad"), NUR_LESEN_ROUTEN)
def test_ist_admin_ueberschreibt_alle_bereichsrechte(engine, client, bereich, get_pfad):
    """``ist_admin=True`` gewährt jeden Bereich, obwohl alle ``recht_*`` auf KEIN stehen."""
    _login_mit_rechten(
        engine,
        client,
        login=f"admin_{bereich}",
        ist_admin=True,
        **{f"recht_{b}": Zugriff.KEIN for b, _ in NUR_LESEN_ROUTEN},
    )
    r = client.get(get_pfad, follow_redirects=False)
    assert r.status_code == OK, (
        f"ist_admin muss {get_pfad} freigeben (recht_{bereich}=kein), war {r.status_code}"
    )


@pytest.mark.parametrize(("bereich", "get_pfad", "post_pfad"), BEREICH_ROUTEN)
def test_ist_admin_darf_auch_schreiben(engine, client, bereich, get_pfad, post_pfad):
    """``ist_admin`` erfüllt auch die Stufe BEARBEITEN ohne gesetzte Einzelrechte."""
    _login_mit_rechten(engine, client, login=f"admin_w_{bereich}", ist_admin=True)
    r = client.post(post_pfad, data={}, follow_redirects=False)
    assert r.status_code != FORBIDDEN, f"POST {post_pfad} darf für ist_admin nicht 403 sein"


# --- 6) Zeitpunkt der Wirkung: Wache frisch, Menü erst nach Re-Login --------


def test_rechteentzug_wirkt_sofort_in_der_router_wache(engine, client):
    """Die Wache liest je Request aus der DB — Entzug greift ohne Re-Login."""
    rollenname = _login_mit_rechten(
        engine,
        client,
        login="lagerist_entzug",
        recht_lager=Zugriff.BEARBEITEN,
        recht_adressen=Zugriff.ANSEHEN,
    )
    assert client.get("/lager/bewegungen").status_code == OK

    _recht_setzen(engine, rollenname, "lager", Zugriff.KEIN)

    r = client.get("/lager/bewegungen", follow_redirects=False)
    assert r.status_code == FORBIDDEN, (
        "Die Router-Wache muss den Entzug sofort durchsetzen (DB, nicht Session)"
    )


def test_rechtezuwachs_wirkt_sofort_in_der_router_wache(engine, client):
    """Umgekehrt: eine Erweiterung greift ebenfalls ohne Re-Login."""
    rollenname = _login_mit_rechten(
        engine, client, login="lagerist_zuwachs", recht_adressen=Zugriff.ANSEHEN
    )
    assert client.get("/lager/bewegungen", follow_redirects=False).status_code == FORBIDDEN

    _recht_setzen(engine, rollenname, "lager", Zugriff.BEARBEITEN)

    assert client.get("/lager/bewegungen").status_code == OK


def test_menuesichtbarkeit_folgt_der_session_und_erst_dem_relogin(engine, client):
    """Bewusste Eigenschaft: Templates lesen die Rechte aus der Session.

    ``rechte_als_dict`` wird beim Login in die Session geschrieben und dort nicht
    nachgeführt (kein DB-Zugriff je Template-Rendering). Ein Rechteentzug bleibt
    daher im Menü unsichtbar, bis sich der Benutzer neu anmeldet — die
    *Durchsetzung* ist davon nicht betroffen (siehe Test oben).
    """
    rollenname = _login_mit_rechten(
        engine,
        client,
        login="lagerist_menue",
        recht_lager=Zugriff.BEARBEITEN,
        recht_adressen=Zugriff.ANSEHEN,
    )
    assert "/lager/bewegungen" in client.get("/").text

    _recht_setzen(engine, rollenname, "lager", Zugriff.KEIN)

    # Session unverändert ⇒ Menüpunkt bleibt sichtbar, obwohl der Aufruf 403 gibt.
    assert "/lager/bewegungen" in client.get("/").text, (
        "Erwartet: veraltete Session-Rechte halten den Menüpunkt sichtbar"
    )
    assert client.get("/lager/bewegungen", follow_redirects=False).status_code == FORBIDDEN

    # Re-Login lädt die Rechte neu ⇒ jetzt verschwindet der Menüpunkt.
    assert client.post("/logout", follow_redirects=False).status_code == REDIRECT
    _anmelden(client, "lagerist_menue")
    assert "/lager/bewegungen" not in client.get("/").text, (
        "Nach Re-Login müssen die neuen Rechte im Menü sichtbar sein"
    )


# --- 7) Abgrenzung: nicht angemeldet ⇒ 401 wird zum Login-Redirect ----------


@pytest.mark.parametrize(("bereich", "get_pfad"), NUR_LESEN_ROUTEN)
def test_ohne_anmeldung_redirect_statt_403(client, bereich, get_pfad):
    """Die Wache hängt an ``get_current_user``: ohne Session gibt es 302 auf /login."""
    r = client.get(get_pfad, follow_redirects=False)
    assert r.status_code == REDIRECT, (
        f"GET {get_pfad} ohne Anmeldung muss 302 auf /login sein, war {r.status_code}"
    )
    assert r.headers["location"] == "/login"

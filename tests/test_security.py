import pytest

from app.config import (
    DEV_SECRET_DEFAULT,
    Settings,
    pruefe_produktionssicherheit,
)


def test_produktion_mit_dev_default_wirft():
    settings = Settings(env="production", secret_key=DEV_SECRET_DEFAULT)
    with pytest.raises(RuntimeError, match="in Produktion gesetzt sein"):
        pruefe_produktionssicherheit(settings)


def test_produktion_mit_zu_kurzem_key_wirft():
    settings = Settings(env="production", secret_key="kurz")
    with pytest.raises(RuntimeError, match="mindestens"):
        pruefe_produktionssicherheit(settings)


def test_produktion_mit_gesetztem_key_ok():
    settings = Settings(env="production", secret_key="ein-hinreichend-langer-geheimer-schluessel")
    pruefe_produktionssicherheit(settings)  # darf nicht werfen


def test_development_mit_dev_default_ok():
    settings = Settings(env="development", secret_key=DEV_SECRET_DEFAULT)
    pruefe_produktionssicherheit(settings)  # in development keine Prüfung

from datetime import time

from iol_bot.config import Config, _env_time


def test_env_time_parses_hh_mm(monkeypatch):
    monkeypatch.setenv("ALGUNA_HORA", "09:05")
    assert _env_time("ALGUNA_HORA", time(0, 0)) == time(9, 5)


def test_env_time_falls_back_to_default_when_missing(monkeypatch):
    monkeypatch.delenv("ALGUNA_HORA", raising=False)
    assert _env_time("ALGUNA_HORA", time(11, 0)) == time(11, 0)


def test_env_time_falls_back_to_default_when_malformed(monkeypatch):
    monkeypatch.setenv("ALGUNA_HORA", "no-es-una-hora")
    assert _env_time("ALGUNA_HORA", time(11, 0)) == time(11, 0)


def test_config_load_defaults_trading_start_time_to_1100(tmp_path, monkeypatch):
    # load_dotenv no pisa una variable que ya esté en el entorno del proceso -- si otro test corrió
    # antes y dejó TRADING_START_TIME seteado, este test no debe depender del orden de ejecución.
    monkeypatch.delenv("TRADING_START_TIME", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("IOL_USERNAME=u\nIOL_PASSWORD=p\n", encoding="utf-8")

    config = Config.load(env_file=env_file)

    assert config.trading_start_time == time(11, 0)


def test_config_load_reads_trading_start_time_from_env(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("IOL_USERNAME=u\nIOL_PASSWORD=p\nTRADING_START_TIME=10:45\n", encoding="utf-8")

    config = Config.load(env_file=env_file)

    assert config.trading_start_time == time(10, 45)

from datetime import date, datetime

from iol_bot.pnl import COMPOSICION_SLOTS, composicion_cartera, daily_pnl_pesos, ganancia_diaria_para_mostrar

HOY = date(2026, 8, 18)


def test_daily_pnl_pesos_positive_variation():
    # Precio subió 10% hoy, valorizado actual 1100 -> ayer valía 1000 -> ganó 100
    assert round(daily_pnl_pesos(1100, 10), 2) == 100.0


def test_daily_pnl_pesos_negative_variation():
    # Precio bajó 10% hoy, valorizado actual 900 -> ayer valía 1000 -> perdió 100
    assert round(daily_pnl_pesos(900, -10), 2) == -100.0


def test_daily_pnl_pesos_zero_variation():
    assert daily_pnl_pesos(1000, 0) == 0.0


def test_daily_pnl_pesos_handles_none():
    assert daily_pnl_pesos(None, 5) == 0.0
    assert daily_pnl_pesos(1000, None) == 0.0


def test_daily_pnl_pesos_handles_total_wipeout_denominator():
    assert daily_pnl_pesos(0, -100) == 0.0


def test_composicion_cartera_incluye_cash_y_posiciones_con_precio_actual():
    positions = {"GGAL": {"cantidad": 10, "costo_promedio": 90.0}, "YPFD": {"cantidad": 5, "costo_promedio": 200.0}}
    precios_cache = {"GGAL": 120.0, "YPFD": 190.0}

    segmentos = composicion_cartera(1000.0, positions, precios_cache)

    valores = {s["nombre"]: s["valor"] for s in segmentos}
    assert valores["Cash disponible"] == 1000.0
    assert valores["GGAL"] == 1200.0  # 10 x 120
    assert valores["YPFD"] == 950.0  # 5 x 190


def test_composicion_cartera_no_confunde_precio_cero_con_precio_ausente():
    # precio=0.0 es "distinto de None" -> se usa tal cual (aunque sea un valor raro), no se
    # confunde con "no hay precio en cache todavía" (que sí cae a costo_promedio).
    positions = {"SIM": {"cantidad": 10, "costo_promedio": 100.0}}
    segmentos = composicion_cartera(0.0, positions, precios_cache={"SIM": 0.0})

    assert next(s for s in segmentos if s["nombre"] == "SIM")["valor"] == 0.0


def test_composicion_cartera_usa_costo_promedio_si_falta_precio_en_cache():
    positions = {"PATH": {"cantidad": 16, "costo_promedio": 11940.0}}
    segmentos = composicion_cartera(0.0, positions, precios_cache={"PATH": None})

    fila_path = next(s for s in segmentos if s["nombre"] == "PATH")
    assert fila_path["valor"] == 16 * 11940.0


def test_composicion_cartera_ordena_por_tamaño_y_asigna_slots_por_orden():
    positions = {
        "CHICA": {"cantidad": 1, "costo_promedio": 100.0},
        "GRANDE": {"cantidad": 1, "costo_promedio": 900.0},
    }
    segmentos = composicion_cartera(500.0, positions, precios_cache={})

    nombres_en_orden = [s["nombre"] for s in segmentos]
    assert nombres_en_orden == ["GRANDE", "Cash disponible", "CHICA"]
    assert next(s for s in segmentos if s["nombre"] == "GRANDE")["color"] == COMPOSICION_SLOTS[0]
    assert next(s for s in segmentos if s["nombre"] == "CHICA")["color"] == COMPOSICION_SLOTS[1]


def test_composicion_cartera_agrupa_en_otros_mas_alla_del_septimo_simbolo():
    # 7 símbolos, pero solo hay 6 slots categóricos -> el más chico debe caer en "Otros".
    positions = {f"SIM{i}": {"cantidad": 1, "costo_promedio": float(100 - i)} for i in range(7)}
    segmentos = composicion_cartera(0.0, positions, precios_cache={})

    nombres = {s["nombre"] for s in segmentos}
    assert "Otros (1)" in nombres
    assert "SIM6" not in nombres  # el de menor valor (100-6=94) quedó agrupado
    # Los 6 símbolos más grandes conservan su propio segmento
    for i in range(6):
        assert f"SIM{i}" in nombres


def test_ganancia_diaria_sin_estado_persistido():
    resultado = ganancia_diaria_para_mostrar(None, hoy=HOY)
    assert resultado == {"estado": "sin_datos", "ganancia": None, "ganancia_pct": None}


def test_ganancia_diaria_de_un_dia_anterior():
    estado = {"baseline_value": 1_000_000.0, "daily_pnl": 500.0, "updated_at": "2026-08-14T16:57:45"}
    resultado = ganancia_diaria_para_mostrar(estado, hoy=HOY)
    assert resultado["estado"] == "otro_dia"
    assert resultado["ganancia"] is None
    assert resultado["ganancia_pct"] is None


def test_ganancia_diaria_esperando_primer_ciclo_sin_romper_por_baseline_none():
    # Caso real del 2026-08-18: RiskManager se autocorrigió hoy (reset_daily) pero todavía no
    # corrió ningún ciclo real -- baseline_value=None, daily_pnl=0.0. Antes de este fix, el
    # dashboard intentaba formatear un ganancia_pct=None y tiraba TypeError.
    estado = {
        "baseline_value": None,
        "daily_pnl": 0.0,
        "updated_at": datetime(2026, 8, 18, 10, 31).isoformat(),
    }
    resultado = ganancia_diaria_para_mostrar(estado, hoy=HOY)
    assert resultado == {"estado": "esperando_primer_ciclo", "ganancia": None, "ganancia_pct": None}


def test_ganancia_diaria_ok_con_baseline_y_pnl_de_hoy():
    estado = {
        "baseline_value": 1_000_000.0,
        "daily_pnl": -5_000.0,
        "updated_at": datetime(2026, 8, 18, 13, 0).isoformat(),
    }
    resultado = ganancia_diaria_para_mostrar(estado, hoy=HOY)
    assert resultado["estado"] == "ok"
    assert resultado["ganancia"] == -5_000.0
    assert resultado["ganancia_pct"] == -0.5


def test_ganancia_diaria_updated_at_malformado_se_trata_como_otro_dia():
    estado = {"baseline_value": 1_000_000.0, "daily_pnl": 100.0, "updated_at": "no-es-una-fecha"}
    resultado = ganancia_diaria_para_mostrar(estado, hoy=HOY)
    assert resultado["estado"] == "otro_dia"

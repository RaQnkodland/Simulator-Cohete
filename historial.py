"""Persistencia y exportacion de misiones del simulador."""

import csv
import json
from pathlib import Path

CAMPOS_MISION = (
    "fecha",
    "planeta",
    "masa_seca_kg",
    "combustible_inicial_kg",
    "empuje_n",
    "consumo_kg_s",
    "angulo_grados",
    "arrastre_cd",
    "area_m2",
    "altura_maxima_km",
    "tiempo_s",
    "velocidad_final_km_s",
    "combustible_restante_kg",
    "resultado",
)


class HistorialMisiones:
    def __init__(self, ruta=None):
        self.ruta = (
            Path(ruta)
            if ruta is not None
            else Path(__file__).with_name("historial_misiones.json")
        )
        self.registros = self._cargar()

    def _cargar(self):
        if not self.ruta.exists():
            return []
        try:
            with self.ruta.open("r", encoding="utf-8") as archivo:
                registros = json.load(archivo)
        except json.JSONDecodeError as error:
            raise ValueError(
                "El archivo de historial no contiene JSON valido."
            ) from error
        if not isinstance(registros, list) or not all(
            isinstance(registro, dict) for registro in registros
        ):
            raise ValueError("El formato del historial de misiones no es valido.")
        return registros

    def agregar(self, registro):
        normalizado = {campo: registro.get(campo, "") for campo in CAMPOS_MISION}
        self.registros.append(normalizado)
        try:
            contenido = json.dumps(
                self.registros, ensure_ascii=False, indent=2, allow_nan=False
            )
            self.ruta.write_text(contenido + "\n", encoding="utf-8")
        except (OSError, TypeError, ValueError):
            self.registros.pop()
            raise

    def exportar_csv(self, ruta):
        with Path(ruta).open("w", newline="", encoding="utf-8-sig") as archivo:
            escritor = csv.DictWriter(archivo, fieldnames=CAMPOS_MISION)
            escritor.writeheader()
            escritor.writerows(self.registros)

"""Modelo gravitatorio y de propulsion para el simulador orbital."""

import math
from dataclasses import dataclass

G = 6.67430e-11


@dataclass(frozen=True)
class Planeta:
    nombre: str
    masa: float
    gravedad: float
    velocidad_angular: float
    densidad_superficial: float
    altura_escala: float


@dataclass(frozen=True)
class Cohete:
    masa_seca: float
    combustible: float
    empuje: float
    consumo: float
    angulo_lanzamiento: float
    coeficiente_arrastre: float
    area_frontal: float


PLANETAS = (
    Planeta("Tierra", 5.972e24, 9.81, 7.2921159e-5, 1.225, 8500.0),
    Planeta("Luna", 7.342e22, 1.62, 2.6617e-6, 0.0, 1.0),
    Planeta("Marte", 6.4171e23, 3.71, 7.088e-5, 0.020, 11100.0),
    Planeta("Jupiter", 1.898e27, 24.79, 1.7585e-4, 0.16, 27000.0),
)


def calcular_parametros(planeta):
    """Calcula el parametro gravitatorio, radio y velocidades de referencia."""
    mu = G * planeta.masa
    radio = math.sqrt(mu / planeta.gravedad)
    if not all(math.isfinite(valor) and valor > 0 for valor in (mu, radio)):
        raise ValueError("Los parametros del planeta producen valores fuera de rango.")
    velocidad_escape = math.sqrt(2 * mu / radio)
    velocidad_rotacion = planeta.velocidad_angular * radio
    if not all(math.isfinite(valor) for valor in (velocidad_escape, velocidad_rotacion)):
        raise ValueError("Los parametros del planeta producen velocidades fuera de rango.")
    return mu, radio, velocidad_escape, velocidad_rotacion


def validar_cohete(cohete):
    valores = (
        cohete.masa_seca,
        cohete.combustible,
        cohete.empuje,
        cohete.consumo,
        cohete.angulo_lanzamiento,
        cohete.coeficiente_arrastre,
        cohete.area_frontal,
    )
    if not all(math.isfinite(valor) for valor in valores):
        raise ValueError("Los parametros del cohete deben ser finitos.")
    if cohete.masa_seca <= 0 or cohete.combustible <= 0:
        raise ValueError("La masa seca y el combustible deben ser mayores que cero.")
    if cohete.empuje <= 0 or cohete.consumo <= 0:
        raise ValueError("El empuje y el consumo deben ser mayores que cero.")
    if not -90 <= cohete.angulo_lanzamiento <= 90:
        raise ValueError("El angulo de lanzamiento debe estar entre -90 y 90 grados.")
    if cohete.coeficiente_arrastre < 0 or cohete.area_frontal <= 0:
        raise ValueError(
            "El arrastre no puede ser negativo y el area frontal debe ser positiva."
        )


def validar_atmosfera(planeta):
    if not math.isfinite(planeta.densidad_superficial) or (
        planeta.densidad_superficial < 0
    ):
        raise ValueError("La densidad atmosferica debe ser finita y no negativa.")
    if not math.isfinite(planeta.altura_escala) or planeta.altura_escala <= 0:
        raise ValueError("La altura de escala atmosferica debe ser positiva.")


def densidad_atmosferica(altitud, planeta):
    validar_atmosfera(planeta)
    if planeta.densidad_superficial == 0:
        return 0.0
    exponente = max(-745.0, -max(0.0, altitud) / planeta.altura_escala)
    return planeta.densidad_superficial * math.exp(exponente)


def energia_especifica(vx, vy, radio, mu):
    """Energia mecanica por unidad de masa en un campo gravitatorio puntual."""
    return 0.5 * (vx * vx + vy * vy) - mu / radio


def calcular_paso_tiempo(radio, mu):
    """Elige un paso que resuelve la orbita y mantiene acotado el calculo."""
    periodo_orbital = 2 * math.pi * math.sqrt(radio * radio * radio / mu)
    dt = min(5.0, periodo_orbital / 1500)
    if not math.isfinite(dt) or dt <= 0:
        raise ValueError("No se pudo calcular un paso de tiempo estable.")
    return dt


def _aceleracion(
    x,
    y,
    vx,
    vy,
    masa,
    fraccion_empuje,
    planeta,
    cohete,
    mu,
    radio_planeta,
):
    radio = math.hypot(x, y)
    if radio == 0 or masa <= 0:
        raise ValueError("La posicion y la masa del cohete deben ser positivas.")

    factor_gravedad = -mu / (radio * radio * radio)
    ax = factor_gravedad * x
    ay = factor_gravedad * y

    altitud = max(0.0, radio - radio_planeta)
    densidad = densidad_atmosferica(altitud, planeta)
    velocidad_aire_x = planeta.velocidad_angular * y
    velocidad_aire_y = -planeta.velocidad_angular * x
    velocidad_relativa_x = vx - velocidad_aire_x
    velocidad_relativa_y = vy - velocidad_aire_y
    rapidez_relativa = math.hypot(velocidad_relativa_x, velocidad_relativa_y)
    if densidad and rapidez_relativa:
        factor_arrastre = (
            -0.5
            * densidad
            * cohete.coeficiente_arrastre
            * cohete.area_frontal
            * rapidez_relativa
            / masa
        )
        ax += factor_arrastre * velocidad_relativa_x
        ay += factor_arrastre * velocidad_relativa_y

    angulo = math.radians(cohete.angulo_lanzamiento)
    aceleracion_empuje = fraccion_empuje * cohete.empuje / masa
    ax += aceleracion_empuje * math.sin(angulo)
    ay += aceleracion_empuje * math.cos(angulo)
    return ax, ay


def _paso_verlet(
    x,
    y,
    vx,
    vy,
    combustible,
    planeta,
    cohete,
    mu,
    radio_planeta,
    dt,
):
    """Avanza el cohete con gravedad, empuje, consumo y arrastre atmosferico."""
    if not all(
        math.isfinite(valor)
        for valor in (x, y, vx, vy, combustible, mu, radio_planeta, dt)
    ):
        raise ValueError("El estado de simulacion debe ser finito.")
    if combustible < 0 or dt <= 0 or mu <= 0 or radio_planeta <= 0:
        raise ValueError("El combustible, paso de tiempo y planeta no son validos.")

    combustible_consumible = min(combustible, cohete.consumo * dt)
    combustible_nuevo = max(0.0, combustible - combustible_consumible)
    masa_media = cohete.masa_seca + (combustible + combustible_nuevo) / 2
    fraccion_empuje = (
        combustible_consumible / (cohete.consumo * dt)
        if combustible_consumible
        else 0.0
    )

    ax, ay = _aceleracion(
        x,
        y,
        vx,
        vy,
        masa_media,
        fraccion_empuje,
        planeta,
        cohete,
        mu,
        radio_planeta,
    )
    nuevo_x = x + vx * dt + 0.5 * ax * dt * dt
    nuevo_y = y + vy * dt + 0.5 * ay * dt * dt
    if math.hypot(nuevo_x, nuevo_y) == 0:
        raise ValueError("El paso de simulacion alcanzo el centro del planeta.")

    velocidad_estimada_x = vx + ax * dt
    velocidad_estimada_y = vy + ay * dt
    nuevo_ax, nuevo_ay = _aceleracion(
        nuevo_x,
        nuevo_y,
        velocidad_estimada_x,
        velocidad_estimada_y,
        masa_media,
        fraccion_empuje,
        planeta,
        cohete,
        mu,
        radio_planeta,
    )
    nuevo_vx = vx + 0.5 * (ax + nuevo_ax) * dt
    nuevo_vy = vy + 0.5 * (ay + nuevo_ay) * dt
    if not all(
        math.isfinite(valor)
        for valor in (nuevo_x, nuevo_y, nuevo_vx, nuevo_vy, combustible_nuevo)
    ):
        raise ValueError("El paso de simulacion excedio el rango numerico.")
    return nuevo_x, nuevo_y, nuevo_vx, nuevo_vy, combustible_nuevo


def paso_fisico(
    x,
    y,
    vx,
    vy,
    combustible,
    planeta,
    cohete,
    mu,
    radio_planeta,
    dt,
):
    """Adapta los subpasos para resolver el arrastre y los cambios de masa."""
    if not math.isfinite(dt) or dt <= 0:
        raise ValueError("El paso de tiempo debe ser positivo y finito.")
    restante = dt
    estado = (x, y, vx, vy, combustible)
    cantidad_subpasos = 0
    while restante > max(1e-12, dt * 1e-12):
        x, y, vx, vy, combustible = estado
        radio = math.hypot(x, y)
        masa = cohete.masa_seca + combustible
        if radio <= 0 or masa <= 0:
            raise ValueError("La posicion y la masa del cohete deben ser positivas.")

        velocidad_aire_x = planeta.velocidad_angular * y
        velocidad_aire_y = -planeta.velocidad_angular * x
        rapidez_relativa = math.hypot(
            vx - velocidad_aire_x, vy - velocidad_aire_y
        )
        densidad = densidad_atmosferica(
            radio - radio_planeta, planeta
        )
        tasa_arrastre = (
            0.5
            * densidad
            * cohete.coeficiente_arrastre
            * cohete.area_frontal
            * rapidez_relativa
            / masa
        )
        dt_subpaso = min(
            restante,
            0.2 / tasa_arrastre if tasa_arrastre > 0 else restante,
        )
        ax, ay = _aceleracion(
            x,
            y,
            vx,
            vy,
            masa,
            1.0 if combustible > 0 else 0.0,
            planeta,
            cohete,
            mu,
            radio_planeta,
        )
        aceleracion = math.hypot(ax, ay)
        if aceleracion > 0:
            dt_subpaso = min(
                dt_subpaso,
                0.2 * math.sqrt(radio / aceleracion),
            )
        if dt_subpaso <= 1e-12:
            raise ValueError("El paso adaptativo de simulacion es demasiado pequeno.")

        estado = _paso_verlet(
            *estado,
            planeta,
            cohete,
            mu,
            radio_planeta,
            dt_subpaso,
        )
        restante -= dt_subpaso
        cantidad_subpasos += 1
        if cantidad_subpasos > 5000:
            raise ValueError("Se excedio el limite de subpasos adaptativos.")
    return estado

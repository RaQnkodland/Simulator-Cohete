"""Simulador grafico de lanzamiento y velocidad de escape."""

import math
import os
import random
import struct
import tempfile
import time
import tkinter as tk
import wave
from datetime import datetime
from io import BytesIO
from tkinter import filedialog, messagebox, ttk

try:
    import winsound
except ImportError:
    winsound = None

from fisica import (
    PLANETAS,
    Cohete,
    Planeta,
    calcular_parametros,
    calcular_paso_tiempo,
    energia_especifica,
    paso_fisico,
    validar_atmosfera,
    validar_cohete,
)
from historial import HistorialMisiones

RADIO_PLANETA_PIXELES = 50
RADIO_VISUAL_MAXIMO = 5
ANCHO_VENTANA = 1400
ALTO_VENTANA = 1000
ANCHO_CANVAS = 680
ALTO_CANVAS = 440
PASOS_POR_CUADRO = 12
MAX_PASOS_SIMULACION = 20000
MAX_TIEMPO_SIMULACION = 7 * 24 * 60 * 60
PAUSA_ANIMACION_MS = 30
FONDO = "#071426"
PANEL = "#0d1d33"
PANEL_SECUNDARIO = "#132a46"
TEXTO = "#edf4ff"
TEXTO_SECUNDARIO = "#91a8c6"
ACENTO = "#5ba8ff"
AMARILLO = "#f4c95d"
FRECUENCIA_AUDIO = 22050


def _empaquetar_wav(muestras):
    buffer = BytesIO()
    with wave.open(buffer, "wb") as archivo:
        archivo.setnchannels(1)
        archivo.setsampwidth(2)
        archivo.setframerate(FRECUENCIA_AUDIO)
        archivo.writeframes(muestras)
    return buffer.getvalue()


def _crear_tono_wav(frecuencia_inicial, frecuencia_final, duracion, volumen):
    cantidad = int(FRECUENCIA_AUDIO * duracion)
    muestras = bytearray()
    for indice in range(cantidad):
        tiempo = indice / FRECUENCIA_AUDIO
        fase = 2 * math.pi * (
            frecuencia_inicial * tiempo
            + (frecuencia_final - frecuencia_inicial)
            * tiempo * tiempo
            / (2 * duracion)
        )
        entrada = min(1.0, indice / (FRECUENCIA_AUDIO * 0.008))
        salida = min(
            1.0,
            (cantidad - indice - 1) / (FRECUENCIA_AUDIO * 0.018),
        )
        muestra = int(
            32767 * volumen * min(entrada, salida) * math.sin(fase)
        )
        muestras.extend(struct.pack("<h", muestra))
    return _empaquetar_wav(muestras)


def _crear_sonido_motor_wav():
    muestras = bytearray()
    for indice in range(FRECUENCIA_AUDIO):
        tiempo = indice / FRECUENCIA_AUDIO
        fase = 2 * math.pi * 58 * tiempo
        modulacion = 0.82 + 0.12 * math.sin(2 * math.pi * 2 * tiempo)
        motor = (
            0.58 * math.sin(fase)
            + 0.25 * math.sin(2 * fase)
            + 0.09 * math.sin(3 * fase)
        )
        muestra = int(32767 * 0.18 * modulacion * motor)
        muestras.extend(struct.pack("<h", muestra))
    return _empaquetar_wav(muestras)


def _crear_fanfarria_wav():
    notas = (523.25, 659.25, 783.99, 1046.5)
    duracion_nota = 0.18
    muestras = bytearray()
    cantidad = int(FRECUENCIA_AUDIO * duracion_nota * len(notas))
    for indice in range(cantidad):
        tiempo = indice / FRECUENCIA_AUDIO
        indice_nota = min(int(tiempo / duracion_nota), len(notas) - 1)
        tiempo_nota = tiempo - indice_nota * duracion_nota
        frecuencia = notas[indice_nota]
        envolvente = min(
            1.0,
            tiempo_nota / 0.012,
            (duracion_nota - tiempo_nota) / 0.035,
        )
        fase = 2 * math.pi * frecuencia * tiempo_nota
        muestra = int(
            32767
            * 0.2
            * envolvente
            * (0.82 * math.sin(fase) + 0.18 * math.sin(2 * fase))
        )
        muestras.extend(struct.pack("<h", muestra))
    return _empaquetar_wav(muestras)


def _crear_explosion_wav():
    generador = random.Random(91)
    muestras = bytearray()
    duracion = 0.85
    cantidad = int(FRECUENCIA_AUDIO * duracion)
    ruido_filtrado = 0.0
    for indice in range(cantidad):
        tiempo = indice / FRECUENCIA_AUDIO
        progreso = tiempo / duracion
        fase = 2 * math.pi * (
            150 * tiempo - 50 * tiempo * tiempo / duracion
        )
        ruido_filtrado = (
            0.78 * ruido_filtrado + 0.22 * (2 * generador.random() - 1)
        )
        envolvente = (1 - progreso) ** 2
        onda = 0.52 * math.sin(fase) + 0.48 * ruido_filtrado
        muestra = int(32767 * 0.32 * envolvente * onda)
        muestras.extend(struct.pack("<h", muestra))
    return _empaquetar_wav(muestras)


class EfectosSonido:
    def __init__(self, root, informar_error):
        self.root = root
        self.informar_error = informar_error
        self.disponible = winsound is not None
        self.silenciado = False
        self.motor_activo = False
        self.reanudar_id = None
        self.ultimo_hover = 0.0
        self.sonidos = {}
        self.directorio_sonidos = None
        if self.disponible:
            datos = {
                "hover": _crear_tono_wav(880, 1180, 0.065, 0.11),
                "click": _crear_tono_wav(520, 360, 0.055, 0.16),
                "lanzamiento": _crear_tono_wav(150, 620, 0.48, 0.2),
                "motor": _crear_sonido_motor_wav(),
                "celebracion": _crear_fanfarria_wav(),
                "explosion": _crear_explosion_wav(),
            }
            self.directorio_sonidos = tempfile.TemporaryDirectory(
                prefix="orbital_audio_"
            )
            for nombre, contenido in datos.items():
                ruta = os.path.join(
                    self.directorio_sonidos.name, "{}.wav".format(nombre)
                )
                with open(ruta, "wb") as archivo:
                    archivo.write(contenido)
                self.sonidos[nombre] = ruta

    def _reproducir(self, sonido, repetir=False):
        if self.silenciado:
            return
        if not self.disponible:
            if not repetir:
                self.root.bell()
            return
        banderas = winsound.SND_FILENAME | winsound.SND_ASYNC
        if repetir:
            banderas |= winsound.SND_LOOP
        try:
            winsound.PlaySound(sonido, banderas)
        except RuntimeError as error:
            self.motor_activo = False
            self.informar_error("No se pudo reproducir el audio: {}".format(error))

    def establecer_silencio(self, silenciado):
        self.silenciado = silenciado
        if silenciado and self.disponible:
            try:
                winsound.PlaySound(None, 0)
            except RuntimeError as error:
                self.informar_error(
                    "No se pudo silenciar el audio: {}".format(error)
                )
        elif self.motor_activo:
            self._reproducir(self.sonidos.get("motor"), repetir=True)

    def hover(self):
        ahora = time.monotonic()
        if self.motor_activo or ahora - self.ultimo_hover < 0.12:
            return
        self.ultimo_hover = ahora
        self._reproducir(self.sonidos.get("hover"))

    def click(self):
        motor_activo = self.motor_activo
        if self.reanudar_id is not None:
            self.root.after_cancel(self.reanudar_id)
            self.reanudar_id = None
        self._reproducir(self.sonidos.get("click"))
        if motor_activo:
            self.reanudar_id = self.root.after(110, self._reanudar_motor)

    def iniciar_lanzamiento(self):
        self._cancelar_reanudacion()
        self.motor_activo = self.disponible
        self._reproducir(self.sonidos.get("lanzamiento"))
        if self.disponible:
            self.reanudar_id = self.root.after(
                480, self._reanudar_motor
            )

    def iniciar_motor(self):
        self._cancelar_reanudacion()
        self.motor_activo = self.disponible
        self._reproducir(self.sonidos.get("motor"), repetir=True)

    def celebrar(self):
        self.detener_motor()
        self._reproducir(self.sonidos.get("celebracion"))

    def explosion(self):
        self.detener_motor()
        self._reproducir(self.sonidos.get("explosion"))

    def _reanudar_motor(self):
        self.reanudar_id = None
        if self.motor_activo:
            self._reproducir(self.sonidos.get("motor"), repetir=True)

    def _cancelar_reanudacion(self):
        if self.reanudar_id is not None:
            self.root.after_cancel(self.reanudar_id)
            self.reanudar_id = None

    def detener_motor(self):
        self.motor_activo = False
        self._cancelar_reanudacion()
        if self.disponible:
            try:
                winsound.PlaySound(None, 0)
            except RuntimeError as error:
                self.informar_error(
                    "No se pudo detener el audio: {}".format(error)
                )

    def cerrar(self):
        self.detener_motor()
        if self.directorio_sonidos is not None:
            self.directorio_sonidos.cleanup()


def dibujar_cohete_canvas(
    canvas, centro_x, centro_y, angulo, escala=1.0, etiqueta="rocket"
):
    def transformar(punto):
        lado, frente = (coordenada * escala for coordenada in punto)
        return (
            centro_x + lado * math.sin(angulo) + frente * math.cos(angulo),
            centro_y + lado * math.cos(angulo) - frente * math.sin(angulo),
        )

    def poligono(puntos, relleno, borde):
        coordenadas = []
        for punto in puntos:
            coordenadas.extend(transformar(punto))
        canvas.create_polygon(
            coordenadas,
            fill=relleno,
            outline=borde,
            width=max(1, escala),
            tags=etiqueta,
        )

    poligono(((0, -25), (-4, -14), (0, -18), (4, -14)), "#ff744f", "#ffb05e")
    poligono(((0, -21), (-2, -14), (0, -17), (2, -14)), "#ffe08a", "")
    poligono(((-5, -5), (-12, -17), (-5, -14)), "#e45d5d", "#ff9b72")
    poligono(((5, -5), (12, -17), (5, -14)), "#e45d5d", "#ff9b72")
    poligono(
        ((0, 23), (-6, 10), (-6, -13), (6, -13), (6, 10)),
        "#e6eef7",
        "#ffffff",
    )
    poligono(((0, 23), (-6, 10), (6, 10)), "#ff765f", "#ffc27d")
    poligono(((-5, -8), (5, -8), (5, -11), (-5, -11)), "#48a9c5", "")

    ventana_x, ventana_y = transformar((0, 2))
    radio_ventana = 3.5 * escala
    canvas.create_oval(
        ventana_x - radio_ventana,
        ventana_y - radio_ventana,
        ventana_x + radio_ventana,
        ventana_y + radio_ventana,
        fill="#52d7f2",
        outline="#f2fbff",
        width=1,
        tags=etiqueta,
    )


class Aplicacion:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("| Simulador de cohetes")
        self.root.geometry("{}x{}".format(ANCHO_VENTANA, ALTO_VENTANA))
        self.root.minsize(1080, 760)
        self.root.configure(bg=FONDO)
        self.root.option_add("*TCombobox*Listbox.background", PANEL_SECUNDARIO)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXTO)

        estilo = ttk.Style(self.root)
        estilo.theme_use("clam")
        estilo.configure(
            "Orbital.TCombobox",
            fieldbackground=PANEL_SECUNDARIO,
            background=PANEL_SECUNDARIO,
            foreground=TEXTO,
            arrowcolor=ACENTO,
            padding=8,
        )
        estilo.map(
            "Orbital.TCombobox",
            fieldbackground=[("readonly", PANEL_SECUNDARIO)],
            foreground=[("readonly", TEXTO)],
        )
        estilo.configure(
            "Orbital.Treeview",
            background=PANEL_SECUNDARIO,
            fieldbackground=PANEL_SECUNDARIO,
            foreground=TEXTO,
            rowheight=30,
            borderwidth=0,
        )
        estilo.configure(
            "Orbital.Treeview.Heading",
            background="#1a3554",
            foreground=TEXTO,
            font=("Segoe UI", 9, "bold"),
        )

        self.contenido = tk.Frame(self.root, bg=FONDO)
        self.contenido.pack(fill="both", expand=True, padx=30, pady=25)
        self.animacion = None
        self.audio_status_var = tk.StringVar(
            value="EFECTOS DE SONIDO ACTIVOS"
            if winsound is not None
            else "Audio limitado en este sistema"
        )
        self.sonidos = EfectosSonido(self.root, self._informar_error_audio)
        self.historial_error = None
        try:
            self.historial = HistorialMisiones()
        except (OSError, ValueError) as error:
            self.historial = None
            self.historial_error = str(error)
            messagebox.showerror(
                "No se pudo cargar el historial",
                "El simulador seguira disponible, pero no se podran guardar ni "
                "comparar misiones.\n\n{}".format(error),
                parent=self.root,
            )
        self.root.protocol("WM_DELETE_WINDOW", self._cerrar)
        self._mostrar_inicio()

    def ejecutar(self):
        self.root.mainloop()

    def _limpiar(self):
        for hijo in self.contenido.winfo_children():
            hijo.destroy()

    def _crear_encabezado(self, titulo, subtitulo, etiqueta=None):
        fila = tk.Frame(self.contenido, bg=FONDO)
        fila.pack(fill="x", pady=(0, 22))
        texto = tk.Frame(fila, bg=FONDO)
        texto.pack(side="left", fill="x", expand=True)
        tk.Label(
            texto,
            text="CONCORDIA UNIVERSAL",
            bg="#10263a",
            fg=ACENTO,
            padx=11,
            pady=5,
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", pady=(0, 10))
        tk.Label(
            texto,
            text=titulo,
            bg=FONDO,
            fg=TEXTO,
            font=("Segoe UI", 23, "bold"),
        ).pack(anchor="w")
        tk.Label(
            texto,
            text=subtitulo,
            bg=FONDO,
            fg=TEXTO_SECUNDARIO,
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(4, 0))
        if etiqueta:
            tk.Label(
                fila,
                text=etiqueta.upper(),
                bg=PANEL,
                fg=ACENTO,
                padx=14,
                pady=9,
                font=("Segoe UI", 9, "bold"),
            ).pack(side="right", anchor="ne", pady=(9, 0))

    def _crear_boton(
        self, padre, texto, comando, principal=False, sonido=True
    ):
        fondo = "#246bce" if principal else "#172b45"
        activo = "#347de0" if principal else "#223b5b"
        def ejecutar():
            comando()
            if sonido:
                self.sonidos.click()

        boton = tk.Button(
            padre,
            text=texto,
            command=ejecutar,
            bg=fondo,
            fg=TEXTO,
            activebackground=activo,
            activeforeground=TEXTO,
            relief="flat",
            bd=0,
            padx=15,
            pady=10,
            cursor="hand2",
            font=("Segoe UI", 10, "bold"),
        )
        boton.bind(
            "<Enter>", lambda evento: self._boton_hover(evento, activo)
        )
        boton.bind(
            "<Leave>", lambda evento: self._boton_salir(evento, fondo)
        )
        return boton

    def _boton_hover(self, evento, color):
        evento.widget.configure(bg=color)
        self.sonidos.hover()

    @staticmethod
    def _boton_salir(evento, color):
        evento.widget.configure(bg=color)

    def _informar_error_audio(self, mensaje):
        self.audio_status_var.set(mensaje)

    def _cerrar(self):
        if self.animacion:
            self.animacion.cancelar_temporizador()
        self.sonidos.cerrar()
        self.root.destroy()

    def _crear_campo(self, padre, etiqueta, variable, unidad):
        grupo = tk.Frame(padre, bg=PANEL)
        grupo.pack(fill="x", pady=(0, 14))
        tk.Label(
            grupo,
            text=etiqueta,
            bg=PANEL,
            fg=TEXTO,
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", pady=(0, 6))
        fila = tk.Frame(grupo, bg=PANEL_SECUNDARIO)
        fila.pack(fill="x")
        entrada = tk.Entry(
            fila,
            textvariable=variable,
            bg=PANEL_SECUNDARIO,
            fg=TEXTO,
            insertbackground=TEXTO,
            relief="flat",
            bd=0,
            font=("Segoe UI", 11),
        )
        entrada.pack(side="left", fill="x", expand=True, padx=(11, 4), pady=10)
        tk.Label(
            fila,
            text=unidad,
            bg=PANEL_SECUNDARIO,
            fg=TEXTO_SECUNDARIO,
            font=("Segoe UI", 9),
        ).pack(side="right", padx=11)
        return entrada

    def _mostrar_inicio(self):
        self._limpiar()
        self.root.unbind_all("<space>")
        self.root.unbind_all("<r>")
        self.root.unbind_all("<R>")
        self.root.unbind_all("<q>")
        self.root.unbind_all("<Q>")
        self.root.bind("<Return>", self._tecla_inicio)
        self._crear_encabezado(
            "Una mision te espera",
            "Explora nuevos mundos y pon a prueba la velocidad de escape.",
            "Centro de exploracion",
        )

        portada = tk.Frame(self.contenido, bg=PANEL, padx=32, pady=30)
        portada.pack(fill="both", expand=True)
        texto = tk.Frame(portada, bg=PANEL)
        texto.pack(side="left", fill="both", expand=True, padx=(0, 25))
        tk.Label(
            texto,
            text="PROGRAMA DE EXPLORACION ESPACIAL",
            bg=PANEL,
            fg=AMARILLO,
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", pady=(8, 12))
        tk.Label(
            texto,
            text="Mas alla del cielo,\nun cohete a la vez.",
            bg=PANEL,
            fg=TEXTO,
            justify="left",
            font=("Segoe UI", 29, "bold"),
        ).pack(anchor="w")
        tk.Label(
            texto,
            text=(
                "Elige un planeta, prepara el lanzamiento y descubre "
                "si tu cohete puede vencer su gravedad."
            ),
            bg=PANEL,
            fg=TEXTO_SECUNDARIO,
            justify="left",
            wraplength=430,
            font=("Segoe UI", 11),
        ).pack(anchor="w", pady=(15, 20))

        destinos = tk.Frame(texto, bg=PANEL)
        destinos.pack(anchor="w", pady=(0, 24))
        for destino in ("TIERRA", "LUNA", "MARTE", "JUPITER"):
            tk.Label(
                destinos,
                text=destino,
                bg="#172b45",
                fg=ACENTO,
                padx=10,
                pady=6,
                font=("Segoe UI", 8, "bold"),
            ).pack(side="left", padx=(0, 7))
        self._crear_boton(
            texto,
            "LANZAR COHETE   >",
            self._mostrar_formulario,
            principal=True,
        ).pack(anchor="w", ipadx=12)
        tk.Label(
            texto,
            textvariable=self.audio_status_var,
            bg=PANEL,
            fg=TEXTO_SECUNDARIO,
            font=("Segoe UI", 8),
        ).pack(anchor="w", pady=(18, 0))

        ilustracion = tk.Canvas(
            portada,
            width=420,
            height=440,
            bg="#08172a",
            highlightthickness=1,
            highlightbackground="#1d3655",
        )
        ilustracion.pack(side="right", fill="y")
        self._dibujar_portada(ilustracion)

    def _dibujar_portada(self, canvas):
        generador = random.Random(29)
        for _ in range(75):
            x = generador.randint(8, 412)
            y = generador.randint(8, 432)
            radio = generador.choice((1, 1, 2))
            color = generador.choice(("#597395", "#94aac5", "#f4c95d"))
            canvas.create_oval(
                x - radio,
                y - radio,
                x + radio,
                y + radio,
                fill=color,
                outline="",
            )
        centro_x, centro_y = 250, 280
        canvas.create_oval(
            centro_x - 132,
            centro_y - 132,
            centro_x + 132,
            centro_y + 132,
            outline="#24486f",
            dash=(3, 7),
            width=2,
        )
        canvas.create_line(
            55, 350, 115, 303, 150, 252, 188, 205,
            fill="#f4c95d",
            dash=(2, 5),
            width=2,
            smooth=True,
        )
        canvas.create_oval(
            centro_x - 71,
            centro_y - 71,
            centro_x + 71,
            centro_y + 71,
            fill="#17699b",
            outline="#5ba8ff",
            width=3,
        )
        for forma in (
            ((-42, -4), (-25, -31), (-7, -25), (-14, -5), (-34, 12)),
            ((8, -31), (33, -22), (42, -5), (24, 2), (13, -11)),
            ((8, 16), (30, 11), (39, 27), (18, 39), (10, 31)),
        ):
            coordenadas = []
            for x, y in forma:
                coordenadas.extend((centro_x + x, centro_y + y))
            canvas.create_polygon(
                coordenadas, fill="#46a879", outline="#17699b"
            )
        canvas.create_oval(
            centro_x - 40,
            centro_y - 49,
            centro_x - 22,
            centro_y - 31,
            fill="#d9f3ff",
            outline="",
        )
        dibujar_cohete_canvas(
            canvas, 145, 172, math.radians(42), escala=1.55, etiqueta="portada"
        )
        canvas.create_text(
            18,
            18,
            text="MISION 01 / SISTEMA SOLAR",
            fill="#a8bfd9",
            anchor="nw",
            font=("Segoe UI", 8, "bold"),
        )

    def _tecla_inicio(self, evento):
        if evento.keysym == "Return":
            self._mostrar_formulario()
            self.sonidos.click()
        return "break"

    def _mostrar_formulario(self):
        self._limpiar()
        self.root.unbind_all("<space>")
        self.root.unbind_all("<r>")
        self.root.unbind_all("<R>")
        self.root.unbind_all("<q>")
        self.root.unbind_all("<Q>")
        self.root.bind("<Return>", self._tecla_enter)

        self._crear_encabezado(
            "Prepara el lanzamiento",
            "Configura el planeta y las condiciones del despegue.",
        )
        tarjetas = tk.Frame(self.contenido, bg=FONDO)
        tarjetas.pack(fill="both", expand=True)
        guia = tk.Frame(tarjetas, bg=PANEL, padx=26, pady=25)
        guia.pack(side="left", fill="both", expand=True, padx=(0, 12))
        formulario = tk.Frame(tarjetas, bg=PANEL, padx=26, pady=25)
        formulario.pack(side="left", fill="both", expand=True, padx=(12, 0))

        tk.Label(
            guia,
            text="Antes de empezar",
            bg=PANEL,
            fg=TEXTO,
            font=("Segoe UI", 17, "bold"),
        ).pack(anchor="w", pady=(0, 8))
        tk.Frame(guia, bg=AMARILLO, height=3, width=46).pack(
            anchor="w", pady=(0, 20)
        )
        instrucciones = (
            ("01", "Elige destino", "Selecciona un planeta o define uno propio."),
            (
                "02",
                "Configura el cohete",
                "Define combustible, empuje, consumo y angulo.",
            ),
            (
                "03",
                "Observa el vuelo",
                "Sigue las graficas, la trayectoria y la telemetria.",
            ),
        )
        for numero, titulo, descripcion in instrucciones:
            paso = tk.Frame(guia, bg=PANEL)
            paso.pack(fill="x", pady=(0, 17))
            tk.Label(
                paso,
                text=numero,
                bg="#1a3454",
                fg=AMARILLO,
                width=4,
                pady=7,
                font=("Segoe UI", 10, "bold"),
            ).pack(side="left", anchor="n", padx=(0, 12))
            detalle = tk.Frame(paso, bg=PANEL)
            detalle.pack(side="left", fill="x", expand=True)
            tk.Label(
                detalle,
                text=titulo,
                bg=PANEL,
                fg=TEXTO,
                font=("Segoe UI", 10, "bold"),
            ).pack(anchor="w")
            tk.Label(
                detalle,
                text=descripcion,
                bg=PANEL,
                fg=TEXTO_SECUNDARIO,
                justify="left",
                wraplength=330,
                font=("Segoe UI", 9),
            ).pack(anchor="w", pady=(3, 0))

        tk.Label(
            guia,
            text="CONTROLES DURANTE LA SIMULACION",
            bg=PANEL,
            fg=ACENTO,
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", pady=(14, 8))
        tk.Label(
            guia,
            text="ESPACIO  Pausar / reanudar     R  Reiniciar     Q  Salir",
            bg="#0b1422",
            fg=TEXTO_SECUNDARIO,
            padx=12,
            pady=12,
            font=("Segoe UI", 9),
        ).pack(fill="x", anchor="w")

        tk.Label(
            formulario,
            text="DATOS DE LA MISION",
            bg=PANEL,
            fg=ACENTO,
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", pady=(0, 8))
        tk.Label(
            formulario,
            text="Parametros de lanzamiento",
            bg=PANEL,
            fg=TEXTO,
            font=("Segoe UI", 17, "bold"),
        ).pack(anchor="w", pady=(0, 18))

        tk.Label(
            formulario,
            text="Planeta",
            bg=PANEL,
            fg=TEXTO,
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", pady=(0, 6))
        self.planeta_var = tk.StringVar(value="Tierra")
        self.planeta_selector = ttk.Combobox(
            formulario,
            textvariable=self.planeta_var,
            values=[planeta.nombre for planeta in PLANETAS] + ["Personalizado"],
            state="readonly",
            style="Orbital.TCombobox",
        )
        self.planeta_selector.pack(fill="x", pady=(0, 14))
        self.planeta_selector.bind(
            "<<ComboboxSelected>>", self._cambiar_planeta
        )

        self.personalizado = tk.Frame(formulario, bg=PANEL)
        self.masa_planeta_var = tk.StringVar(value="5.972e24")
        self.gravedad_var = tk.StringVar(value="9.81")
        self.rotacion_var = tk.StringVar(value="7.2921159e-5")
        self.densidad_var = tk.StringVar(value="1.225")
        self.altura_escala_var = tk.StringVar(value="8500")
        self.personalizado.grid_columnconfigure(0, weight=1)
        self.personalizado.grid_columnconfigure(1, weight=1)
        for indice, (etiqueta, variable, unidad) in enumerate(
            (
                ("Masa del planeta", self.masa_planeta_var, "kg"),
                ("Gravedad superficial", self.gravedad_var, "m/s2"),
                ("Velocidad angular", self.rotacion_var, "rad/s"),
                ("Densidad atmosferica", self.densidad_var, "kg/m3"),
                ("Altura de escala", self.altura_escala_var, "m"),
            )
        ):
            campo = tk.Frame(self.personalizado, bg=PANEL)
            campo.grid(
                row=indice // 2,
                column=indice % 2,
                sticky="ew",
                padx=(0, 10),
            )
            self._crear_campo(campo, etiqueta, variable, unidad)

        self.masa_seca_var = tk.StringVar(value="50000")
        self.combustible_var = tk.StringVar(value="500000")
        self.empuje_var = tk.StringVar(value="7000000")
        self.consumo_var = tk.StringVar(value="350")
        self.angulo_var = tk.StringVar(value="0")
        self.arrastre_var = tk.StringVar(value="0.5")
        self.area_var = tk.StringVar(value="10")
        parametros_cohete = tk.Frame(formulario, bg=PANEL)
        parametros_cohete.pack(fill="x")
        parametros_cohete.grid_columnconfigure(0, weight=1)
        parametros_cohete.grid_columnconfigure(1, weight=1)
        campos_cohete = (
            ("Masa seca", self.masa_seca_var, "kg"),
            ("Combustible", self.combustible_var, "kg"),
            ("Empuje del motor", self.empuje_var, "N"),
            ("Consumo", self.consumo_var, "kg/s"),
            ("Angulo desde vertical", self.angulo_var, "grados"),
            ("Coeficiente de arrastre", self.arrastre_var, "Cd"),
            ("Area frontal", self.area_var, "m2"),
        )
        for indice, (etiqueta, variable, unidad) in enumerate(campos_cohete):
            campo = tk.Frame(parametros_cohete, bg=PANEL)
            campo.grid(
                row=indice // 2,
                column=indice % 2,
                sticky="ew",
                padx=(0, 10),
            )
            self._crear_campo(campo, etiqueta, variable, unidad)
        tk.Label(
            formulario,
            text="El combustible se consume hasta agotarse; el empuje sigue el angulo seleccionado.",
            bg=PANEL,
            fg=TEXTO_SECUNDARIO,
            wraplength=450,
            justify="left",
            font=("Segoe UI", 8),
        ).pack(anchor="w", pady=(0, 10))

        self.error_var = tk.StringVar(value="")
        self.error_label = tk.Label(
            formulario,
            textvariable=self.error_var,
            bg=PANEL,
            fg="#ff7187",
            justify="left",
            wraplength=420,
            font=("Segoe UI", 9),
        )
        self.error_label.pack(fill="x", anchor="w", pady=(0, 8))
        self._crear_boton(
            formulario,
            "INICIAR SIMULACION",
            self._iniciar_simulacion,
            principal=True,
            sonido=False,
        ).pack(fill="x")
        self._crear_boton(
            formulario,
            "VOLVER AL INICIO",
            self._mostrar_inicio,
        ).pack(fill="x", pady=(8, 0))
        self._crear_boton(
            formulario,
            "HISTORIAL Y COMPARACION",
            self._mostrar_historial,
        ).pack(fill="x", pady=(8, 0))
        self._cambiar_planeta()

    def _cambiar_planeta(self, evento=None):
        if evento is not None and evento.widget is not self.planeta_selector:
            return
        if self.planeta_var.get() == "Personalizado":
            self.personalizado.pack(fill="x", before=self.error_label)
        else:
            self.personalizado.pack_forget()

    @staticmethod
    def _leer_numero(texto, etiqueta, permitir_cero=False):
        try:
            valor = float(texto.strip().replace(",", "."))
        except ValueError:
            raise ValueError("{} debe ser un numero valido.".format(etiqueta))
        if not math.isfinite(valor) or valor < 0 or (
            valor == 0 and not permitir_cero
        ):
            condicion = "mayor o igual que cero" if permitir_cero else "mayor que cero"
            raise ValueError(
                "{} debe ser finito y {}.".format(etiqueta, condicion)
            )
        return valor

    @staticmethod
    def _leer_angulo(texto):
        try:
            valor = float(texto.strip().replace(",", "."))
        except ValueError:
            raise ValueError("El angulo debe ser un numero valido.")
        if not math.isfinite(valor):
            raise ValueError("El angulo debe ser finito.")
        return valor

    def _obtener_planeta(self):
        nombre = self.planeta_var.get()
        if nombre != "Personalizado":
            for planeta in PLANETAS:
                if planeta.nombre == nombre:
                    return planeta
            raise ValueError("Selecciona un planeta de la lista.")
        return Planeta(
            "Personalizado",
            self._leer_numero(self.masa_planeta_var.get(), "La masa del planeta"),
            self._leer_numero(self.gravedad_var.get(), "La gravedad"),
            self._leer_numero(
                self.rotacion_var.get(),
                "La velocidad angular",
                permitir_cero=True,
            ),
            self._leer_numero(
                self.densidad_var.get(),
                "La densidad atmosferica",
                permitir_cero=True,
            ),
            self._leer_numero(
                self.altura_escala_var.get(), "La altura de escala atmosferica"
            ),
        )

    def _iniciar_simulacion(self):
        self.error_var.set("")
        try:
            planeta = self._obtener_planeta()
            cohete = Cohete(
                masa_seca=self._leer_numero(
                    self.masa_seca_var.get(), "La masa seca"
                ),
                combustible=self._leer_numero(
                    self.combustible_var.get(), "El combustible"
                ),
                empuje=self._leer_numero(
                    self.empuje_var.get(), "El empuje"
                ),
                consumo=self._leer_numero(
                    self.consumo_var.get(), "El consumo"
                ),
                angulo_lanzamiento=self._leer_angulo(self.angulo_var.get()),
                coeficiente_arrastre=self._leer_numero(
                    self.arrastre_var.get(),
                    "El coeficiente de arrastre",
                    permitir_cero=True,
                ),
                area_frontal=self._leer_numero(
                    self.area_var.get(), "El area frontal"
                ),
            )
            validar_cohete(cohete)
            validar_atmosfera(planeta)
            mu, radio, velocidad_escape, velocidad_rotacion = calcular_parametros(
                planeta
            )
            dt = calcular_paso_tiempo(radio, mu)
            energia_cinetica = (
                0.5
                * (cohete.masa_seca + cohete.combustible)
                * velocidad_rotacion ** 2
            )
            if not all(
                math.isfinite(valor)
                for valor in (
                    dt,
                    energia_cinetica,
                )
            ):
                raise ValueError("Los datos exceden el rango numerico.")
        except (ValueError, OverflowError) as error:
            self.error_var.set(str(error))
            return

        prediccion = "Motor activo; trayectoria determinada durante el vuelo."
        self._mostrar_simulacion(
            planeta,
            cohete,
            velocidad_escape,
            energia_cinetica,
            prediccion,
        )

    def _tarjeta_metrica(self, padre, titulo, variable):
        tarjeta = tk.Frame(padre, bg=PANEL_SECUNDARIO, padx=11, pady=7)
        tk.Label(
            tarjeta,
            text=titulo.upper(),
            bg=PANEL_SECUNDARIO,
            fg=TEXTO_SECUNDARIO,
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w")
        tk.Label(
            tarjeta,
            textvariable=variable,
            bg=PANEL_SECUNDARIO,
            fg=TEXTO,
            font=("Segoe UI", 12, "bold"),
        ).pack(anchor="w", pady=(4, 0))
        return tarjeta

    def _mostrar_simulacion(
        self,
        planeta,
        cohete,
        velocidad_escape,
        energia_cinetica,
        prediccion,
    ):
        self._limpiar()
        self.root.unbind("<Return>")
        self._crear_encabezado(
            "Simulacion en curso",
            "Observa la trayectoria y consulta la telemetria del lanzamiento.",
            planeta.nombre,
        )
        cuerpo = tk.Frame(self.contenido, bg=FONDO)
        cuerpo.pack(fill="both", expand=True)
        cuerpo.grid_columnconfigure(0, weight=1)
        cuerpo.grid_rowconfigure(0, weight=4)
        cuerpo.grid_rowconfigure(1, weight=0)
        cuerpo.grid_rowconfigure(2, weight=1)

        self.canvas = tk.Canvas(
            cuerpo,
            width=ANCHO_CANVAS,
            height=ALTO_CANVAS,
            bg="#050b18",
            highlightthickness=1,
            highlightbackground="#1d3048",
        )
        self.canvas.grid(
            row=0, column=0, sticky="nsew", padx=(0, 16), pady=(0, 10)
        )
        self.velocidad_simulacion_var = tk.DoubleVar(value=1.0)
        self.zoom_var = tk.DoubleVar(value=1.0)
        controles_vista = tk.Frame(cuerpo, bg=PANEL, padx=12, pady=5)
        controles_vista.grid(
            row=1, column=0, sticky="ew", padx=(0, 16), pady=(0, 10)
        )
        controles_vista.grid_columnconfigure(0, weight=1)
        controles_vista.grid_columnconfigure(1, weight=1)
        for columna, (titulo, variable, minimo, maximo, paso, comando) in enumerate(
            (
                (
                    "VELOCIDAD DE SIMULACION",
                    self.velocidad_simulacion_var,
                    1,
                    10,
                    1,
                    None,
                ),
                (
                    "ZOOM DE CAMARA",
                    self.zoom_var,
                    0.6,
                    2.0,
                    0.1,
                    self._cambiar_zoom,
                ),
            )
        ):
            control = tk.Frame(controles_vista, bg=PANEL)
            control.grid(
                row=0,
                column=columna,
                sticky="ew",
                padx=(0, 12) if columna == 0 else (12, 0),
            )
            tk.Label(
                control,
                text=titulo,
                bg=PANEL,
                fg=TEXTO_SECUNDARIO,
                font=("Segoe UI", 8, "bold"),
            ).pack(anchor="w")
            opciones = {
                "from_": minimo,
                "to": maximo,
                "resolution": paso,
                "orient": "horizontal",
                "variable": variable,
                "bg": PANEL,
                "fg": TEXTO,
                "troughcolor": PANEL_SECUNDARIO,
                "activebackground": ACENTO,
                "highlightthickness": 0,
                "showvalue": True,
            }
            if comando is not None:
                opciones["command"] = comando
            tk.Scale(control, **opciones).pack(fill="x")

        self.grafica_canvas = tk.Canvas(
            cuerpo,
            height=140,
            bg=PANEL,
            highlightthickness=1,
            highlightbackground="#1d3048",
        )
        self.grafica_canvas.grid(
            row=2, column=0, sticky="nsew", padx=(0, 16)
        )

        panel = tk.Frame(cuerpo, bg=PANEL, width=305, padx=16, pady=16)
        panel.grid(row=0, column=1, rowspan=3, sticky="ns")
        panel.grid_propagate(False)
        tk.Label(
            panel,
            text="TELEMETRIA",
            bg=PANEL,
            fg=ACENTO,
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", pady=(0, 10))

        self.estado_var = tk.StringVar(value="EN VUELO")
        self.estado_label = tk.Label(
            panel,
            textvariable=self.estado_var,
            bg="#17314a",
            fg="#72e0a7",
            padx=10,
            pady=8,
            font=("Segoe UI", 9, "bold"),
        )
        self.estado_label.pack(fill="x", pady=(0, 12))

        self.tiempo_var = tk.StringVar(value="0.0 h")
        self.altura_var = tk.StringVar(value="0 km")
        self.altura_maxima_var = tk.StringVar(value="0 km")
        self.velocidad_actual_var = tk.StringVar(value="0.00 km/s")
        self.combustible_actual_var = tk.StringVar(
            value="{:,.0f} kg".format(cohete.combustible)
        )
        metricas = tk.Frame(panel, bg=PANEL)
        metricas.pack(fill="x")
        metricas.grid_columnconfigure(0, weight=1)
        metricas.grid_columnconfigure(1, weight=1)
        for indice, (titulo, variable) in enumerate(
            (
                ("Tiempo simulado", self.tiempo_var),
                ("Altura actual", self.altura_var),
                ("Altura maxima", self.altura_maxima_var),
                ("Velocidad actual", self.velocidad_actual_var),
                ("Combustible restante", self.combustible_actual_var),
            )
        ):
            self._tarjeta_metrica(metricas, titulo, variable).grid(
                row=indice // 2,
                column=indice % 2,
                sticky="nsew",
                padx=(0, 5) if indice % 2 == 0 else (5, 0),
                pady=(0, 6),
            )

        self.resultado_var = tk.StringVar(value=prediccion)
        tk.Label(
            panel,
            text="PREDICCION INICIAL",
            bg=PANEL,
            fg=TEXTO_SECUNDARIO,
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w", pady=(3, 4))
        tk.Label(
            panel,
            textvariable=self.resultado_var,
            bg=PANEL,
            fg=AMARILLO,
            justify="left",
            wraplength=245,
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", pady=(0, 8))
        tk.Label(
            panel,
            text=(
                "Escape de referencia: {:.2f} km/s\n"
                "Energia cinetica inicial: {:.2e} J\n"
                "Combustion estimada: {:.1f} min"
            ).format(
                velocidad_escape / 1000,
                energia_cinetica,
                cohete.combustible / cohete.consumo / 60,
            ),
            bg=PANEL,
            fg=TEXTO_SECUNDARIO,
            justify="left",
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(0, 12))

        pie_panel = tk.Frame(panel, bg=PANEL)
        pie_panel.pack(fill="x", side="bottom")
        tk.Label(
            pie_panel,
            textvariable=self.audio_status_var,
            bg=PANEL,
            fg=TEXTO_SECUNDARIO,
            wraplength=245,
            justify="left",
            font=("Segoe UI", 8),
        ).pack(anchor="w", side="bottom", pady=(0, 8))
        tk.Label(
            pie_panel,
            text="Atajos: ESPACIO pausa, R reinicia, Q sale.",
            bg=PANEL,
            fg=TEXTO_SECUNDARIO,
            wraplength=245,
            justify="left",
            font=("Segoe UI", 8),
        ).pack(anchor="w", side="bottom", pady=(0, 12))

        botones = tk.Frame(pie_panel, bg=PANEL)
        botones.pack(fill="x", side="bottom", pady=(0, 10))
        botones.grid_columnconfigure(0, weight=1)
        botones.grid_columnconfigure(1, weight=1)
        self.boton_pausa = self._crear_boton(
            botones, "PAUSAR", self._alternar_pausa, principal=True
        )
        self.boton_pausa.grid(row=0, column=0, sticky="ew", padx=(0, 5), pady=3)
        self._crear_boton(
            botones,
            "REINICIAR",
            self._reiniciar_simulacion,
            sonido=False,
        ).grid(row=0, column=1, sticky="ew", padx=(5, 0), pady=3)
        self.boton_silencio = self._crear_boton(
            botones, "SILENCIAR AUDIO", self._alternar_silencio
        )
        self.boton_silencio.grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=3
        )
        self._crear_boton(
            botones,
            "VOLVER A CONFIGURAR",
            self._volver_a_configuracion,
            principal=True,
        ).grid(row=2, column=0, columnspan=2, sticky="ew", pady=3)

        self.animacion = Animacion(
            self,
            planeta,
            cohete,
        )
        self.sonidos.iniciar_lanzamiento()
        self.root.bind_all("<space>", self._tecla_pausa)
        self.root.bind_all("<r>", self._tecla_reiniciar)
        self.root.bind_all("<R>", self._tecla_reiniciar)
        self.root.bind_all("<q>", self._tecla_salir)
        self.root.bind_all("<Q>", self._tecla_salir)
        self.root.focus_set()
        self.animacion.programar_actualizacion(100)

    def _cambiar_zoom(self, valor):
        if self.animacion:
            self.animacion.cambiar_zoom(float(valor))

    def _alternar_silencio(self):
        silenciado = not self.sonidos.silenciado
        self.sonidos.establecer_silencio(silenciado)
        self.boton_silencio.configure(
            text="ACTIVAR AUDIO" if silenciado else "SILENCIAR AUDIO"
        )

    def _guardar_mision(self, animacion, resultado):
        if self.historial is None:
            messagebox.showerror(
                "Historial no disponible",
                "No se pudo guardar esta mision.\n\n{}".format(
                    self.historial_error or "El historial no esta disponible."
                ),
                parent=self.root,
            )
            return
        registro = {
            "fecha": datetime.now().astimezone().isoformat(timespec="seconds"),
            "planeta": animacion.planeta.nombre,
            "masa_seca_kg": animacion.cohete.masa_seca,
            "combustible_inicial_kg": animacion.cohete.combustible,
            "empuje_n": animacion.cohete.empuje,
            "consumo_kg_s": animacion.cohete.consumo,
            "angulo_grados": animacion.cohete.angulo_lanzamiento,
            "arrastre_cd": animacion.cohete.coeficiente_arrastre,
            "area_m2": animacion.cohete.area_frontal,
            "altura_maxima_km": animacion.altura_maxima / 1000,
            "tiempo_s": animacion.tiempo,
            "velocidad_final_km_s": math.hypot(
                animacion.vx, animacion.vy
            ) / 1000,
            "combustible_restante_kg": animacion.combustible_restante,
            "resultado": resultado,
        }
        try:
            self.historial.agregar(registro)
        except (OSError, TypeError, ValueError) as error:
            messagebox.showerror(
                "No se pudo guardar la mision",
                "La simulacion termino, pero el historial no se actualizo.\n\n"
                "{}".format(error),
                parent=self.root,
            )

    def _mostrar_historial(self):
        self._limpiar()
        self.root.unbind("<Return>")
        self._crear_encabezado(
            "Historial de misiones",
            "Compara resultados guardados entre ejecuciones y exportalos a CSV.",
        )
        tabla_marco = tk.Frame(self.contenido, bg=PANEL, padx=14, pady=14)
        tabla_marco.pack(fill="both", expand=True)
        columnas = (
            "fecha",
            "planeta",
            "altura_maxima_km",
            "tiempo_s",
            "velocidad_final_km_s",
            "resultado",
        )
        tabla = ttk.Treeview(
            tabla_marco,
            columns=columnas,
            show="headings",
            height=16,
            style="Orbital.Treeview",
        )
        encabezados = {
            "fecha": ("Fecha", 175),
            "planeta": ("Destino", 100),
            "altura_maxima_km": ("Altura maxima (km)", 140),
            "tiempo_s": ("Tiempo (s)", 100),
            "velocidad_final_km_s": ("Velocidad final (km/s)", 150),
            "resultado": ("Resultado", 280),
        }
        for columna, (titulo, ancho) in encabezados.items():
            tabla.heading(columna, text=titulo)
            tabla.column(columna, width=ancho, anchor="w")
        tabla.tag_configure("par", background=PANEL_SECUNDARIO, foreground=TEXTO)
        tabla.tag_configure("impar", background="#10243a", foreground=TEXTO)
        if self.historial is not None:
            for indice, registro in enumerate(reversed(self.historial.registros)):
                tabla.insert(
                    "",
                    "end",
                    values=(
                        registro.get("fecha", ""),
                        registro.get("planeta", ""),
                        "{:,.2f}".format(
                            registro.get("altura_maxima_km", 0) or 0
                        ),
                        "{:,.1f}".format(registro.get("tiempo_s", 0) or 0),
                        "{:.3f}".format(
                            registro.get("velocidad_final_km_s", 0) or 0
                        ),
                        registro.get("resultado", ""),
                    ),
                    tags=("par" if indice % 2 == 0 else "impar",),
                )
        tabla.pack(side="left", fill="both", expand=True)
        barra = ttk.Scrollbar(
            tabla_marco, orient="vertical", command=tabla.yview
        )
        tabla.configure(yscrollcommand=barra.set)
        barra.pack(side="right", fill="y")
        if self.historial is None:
            tk.Label(
                self.contenido,
                text="Historial no disponible: {}".format(self.historial_error),
                bg=FONDO,
                fg="#ff7187",
                font=("Segoe UI", 9),
            ).pack(anchor="w", pady=(8, 0))
        elif not self.historial.registros:
            tk.Label(
                self.contenido,
                text="Aun no hay misiones guardadas. Ejecuta una simulacion para comenzar.",
                bg=FONDO,
                fg=TEXTO_SECUNDARIO,
                font=("Segoe UI", 9),
            ).pack(anchor="w", pady=(8, 0))
        acciones = tk.Frame(self.contenido, bg=FONDO)
        acciones.pack(fill="x", pady=(12, 0))
        self._crear_boton(
            acciones, "EXPORTAR CSV", self._exportar_historial, principal=True
        ).pack(side="left")
        self._crear_boton(
            acciones, "VOLVER A LA MISION", self._mostrar_formulario
        ).pack(side="right")

    def _exportar_historial(self):
        if self.historial is None:
            messagebox.showerror(
                "Historial no disponible",
                self.historial_error or "El historial no esta disponible.",
                parent=self.root,
            )
            return
        ruta = filedialog.asksaveasfilename(
            parent=self.root,
            title="Exportar historial de misiones",
            defaultextension=".csv",
            initialfile="misiones_cohete.csv",
            filetypes=(("Archivo CSV", "*.csv"), ("Todos los archivos", "*.*")),
        )
        if not ruta:
            return
        try:
            self.historial.exportar_csv(ruta)
        except OSError as error:
            messagebox.showerror(
                "No se pudo exportar el CSV",
                "No se escribio ningun archivo CSV.\n\n{}".format(error),
                parent=self.root,
            )

    def _alternar_pausa(self):
        if self.animacion:
            self.animacion.cambiar_pausa()

    def _reiniciar_simulacion(self):
        if self.animacion:
            self.animacion.reiniciar()

    def _volver_a_configuracion(self):
        if self.animacion:
            self.animacion.cancelar_temporizador()
        self.sonidos.detener_motor()
        self.animacion = None
        self._mostrar_formulario()

    def _tecla_pausa(self, evento):
        if evento.keysym == "space":
            self._alternar_pausa()
        return "break"

    def _tecla_reiniciar(self, evento):
        if evento.keysym.lower() == "r":
            self._reiniciar_simulacion()
        return "break"

    def _tecla_salir(self, evento):
        if evento.keysym.lower() == "q":
            self._cerrar()
        return "break"

    def _tecla_enter(self, evento):
        if evento.keysym == "Return":
            self._iniciar_simulacion()
        return "break"


class Animacion:
    def __init__(self, aplicacion, planeta, cohete):
        self.aplicacion = aplicacion
        self.canvas = aplicacion.canvas
        self.planeta = planeta
        self.cohete = cohete
        self.combustible_restante = cohete.combustible
        self.mu, self.radio, self.velocidad_escape, self.velocidad_rotacion = (
            calcular_parametros(planeta)
        )
        self.dt = calcular_paso_tiempo(self.radio, self.mu)
        self.escala = RADIO_PLANETA_PIXELES / self.radio
        self.zoom_actual = 1.0
        self.canvas.update_idletasks()
        self.ancho_canvas = max(ANCHO_CANVAS, self.canvas.winfo_width())
        self.alto_canvas = max(ALTO_CANVAS, self.canvas.winfo_height())
        self.centro_x = self.ancho_canvas / 2
        self.centro_y = self.alto_canvas / 2
        self.radio_visual_pixeles = max(
            RADIO_PLANETA_PIXELES,
            min(
                RADIO_VISUAL_MAXIMO * RADIO_PLANETA_PIXELES,
                self.ancho_canvas / 2 - 15,
                self.alto_canvas / 2 - 15,
            ),
        )
        self.pasos = 0
        self.tiempo = 0.0
        self.altura_maxima = 0.0
        self.muestras_grafica = []
        self.trayectoria = []
        self.pausado = False
        self.terminado = False
        self.mision_guardada = False
        self.after_id = None
        self.efecto_id = None
        self.prediccion_inicial = aplicacion.resultado_var.get()
        self._dibujar_escena()
        self.reiniciar_estado()

    def _dibujar_escena(self):
        self.canvas.delete("all")
        generador = random.Random(17)
        colores_estrellas = ("#526985", "#8195ad", "#d0d9e6")
        for _ in range(110):
            x = generador.randint(8, self.ancho_canvas - 8)
            y = generador.randint(8, self.alto_canvas - 8)
            radio = generador.choice((1, 1, 2))
            color = generador.choice(colores_estrellas)
            self.canvas.create_oval(
                x - radio,
                y - radio,
                x + radio,
                y + radio,
                fill=color,
                outline="",
            )

        radio_visual = self.radio_visual_pixeles
        self.canvas.create_oval(
            self.centro_x - radio_visual,
            self.centro_y - radio_visual,
            self.centro_x + radio_visual,
            self.centro_y + radio_visual,
            outline="#203a58",
            dash=(3, 7),
            width=1,
        )
        inicio_planeta = len(self.canvas.find_all())
        colores = {
            "Tierra": ("#17699b", "#46a879"),
            "Luna": ("#838b9a", "#b9c0cc"),
            "Marte": ("#a94738", "#dc7957"),
            "Jupiter": ("#b56d42", "#e0ae70"),
        }
        color_base, color_detalle = colores.get(
            self.planeta.nombre, ("#4a5eaa", "#8497e8")
        )
        radio_planeta = RADIO_PLANETA_PIXELES
        self.canvas.create_oval(
            self.centro_x - radio_planeta - 6,
            self.centro_y - radio_planeta - 6,
            self.centro_x + radio_planeta + 6,
            self.centro_y + radio_planeta + 6,
            outline="#38bdf8",
            width=2,
        )
        self.canvas.create_oval(
            self.centro_x - radio_planeta,
            self.centro_y - radio_planeta,
            self.centro_x + radio_planeta,
            self.centro_y + radio_planeta,
            fill=color_base,
            outline="#0c3150",
            width=2,
        )

        if self.planeta.nombre == "Tierra":
            formas = (
                (
                    (-31, 14), (-22, 26), (-9, 25), (-4, 17),
                    (-13, 10), (-18, 1), (-29, 5),
                ),
                (
                    (7, 18), (18, 25), (30, 18), (26, 9),
                    (17, 7), (13, -2), (5, 3),
                ),
                (
                    (12, -13), (25, -9), (30, -19), (20, -27),
                    (10, -22), (6, -17),
                ),
            )
            for forma in formas:
                coordenadas = []
                for x, y in forma:
                    coordenadas.extend(
                        (self.centro_x + x, self.centro_y - y)
                    )
                self.canvas.create_polygon(
                    coordenadas,
                    fill=color_detalle,
                    outline=color_base,
                )
        elif self.planeta.nombre == "Jupiter":
            for y in (-27, -12, 6, 23):
                semiancho = math.sqrt(radio_planeta ** 2 - y ** 2) - 5
                self.canvas.create_line(
                    self.centro_x - semiancho,
                    self.centro_y - y,
                    self.centro_x + semiancho,
                    self.centro_y - y,
                    fill=color_detalle,
                    width=4,
                )
        elif self.planeta.nombre == "Luna":
            for x, y, radio in ((-19, 15, 7), (18, -6, 10), (-5, -28, 5)):
                self.canvas.create_oval(
                    self.centro_x + x - radio,
                    self.centro_y - y - radio,
                    self.centro_x + x + radio,
                    self.centro_y - y + radio,
                    outline=color_detalle,
                    width=2,
                )

        self.canvas.create_oval(
            self.centro_x - 20,
            self.centro_y - 29,
            self.centro_x - 8,
            self.centro_y - 17,
            fill="#d9f3ff",
            outline="",
        )
        for elemento in self.canvas.find_all()[inicio_planeta:]:
            self.canvas.addtag_withtag("planet", elemento)
        self.canvas.create_text(
            18,
            18,
            text="CAMPO GRAVITATORIO",
            fill="#7891ad",
            anchor="nw",
            font=("Segoe UI", 8, "bold"),
        )
        self.canvas.create_text(
            self.ancho_canvas - 18,
            self.alto_canvas - 18,
            text="ZONA DE SEGUIMIENTO",
            fill="#7891ad",
            anchor="se",
            font=("Segoe UI", 8, "bold"),
        )

    def _posicion_visual(self):
        return self._posicion_mundo(self.x, self.y)

    def _posicion_mundo(self, x, y):
        radio = math.hypot(x, y)
        zoom = self.aplicacion.zoom_var.get()
        radio_maximo_visual = (
            self.radio_visual_pixeles
            / RADIO_PLANETA_PIXELES
            * self.radio
        )
        factor_visual = min(
            1.0,
            radio_maximo_visual / (zoom * max(radio, self.radio)),
        )
        return (
            self.centro_x + x * factor_visual * self.escala * zoom,
            self.centro_y - y * factor_visual * self.escala * zoom,
        )

    def redibujar_trayectoria(self):
        self.canvas.delete("trajectory")
        if len(self.trayectoria) < 2:
            return
        intervalo = max(1, len(self.trayectoria) // 5000)
        puntos = self.trayectoria[::intervalo]
        if puntos[-1] != self.trayectoria[-1]:
            puntos.append(self.trayectoria[-1])
        for anterior, actual in zip(puntos, puntos[1:]):
            inicio = self._posicion_mundo(*anterior)
            fin = self._posicion_mundo(*actual)
            self.canvas.create_line(
                inicio[0],
                inicio[1],
                fin[0],
                fin[1],
                fill="#f3a55b",
                width=2,
                tags="trajectory",
            )

    def cambiar_zoom(self, zoom):
        factor = zoom / self.zoom_actual
        self.canvas.scale("planet", self.centro_x, self.centro_y, factor, factor)
        self.zoom_actual = zoom
        self.redibujar_trayectoria()
        self._dibujar_cohete()

    def _dibujar_cohete(self):
        self.canvas.delete("rocket")
        centro_x, centro_y = self._posicion_visual()
        angulo = math.pi / 2 - math.radians(self.cohete.angulo_lanzamiento)
        dibujar_cohete_canvas(
            self.canvas,
            centro_x,
            centro_y,
            angulo,
            etiqueta="rocket",
        )

    def reiniciar_estado(self):
        self.x, self.y = 0.0, self.radio
        self.vx, self.vy = self.velocidad_rotacion, 0.0
        self.combustible_restante = self.cohete.combustible
        self.pasos = 0
        self.tiempo = 0.0
        self.altura_maxima = 0.0
        self.muestras_grafica = []
        self.pausado = False
        self.terminado = False
        self.mision_guardada = False
        self.trayectoria = [(self.x, self.y)]
        self.canvas.delete("celebracion", "explosion")
        self.canvas.delete("trajectory")
        self.aplicacion.grafica_canvas.delete("all")
        self._dibujar_cohete()
        self.aplicacion.estado_var.set("EN VUELO")
        self.aplicacion.estado_label.configure(fg="#72e0a7")
        self.aplicacion.boton_pausa.configure(text="PAUSAR")
        self.aplicacion.boton_pausa.configure(state="normal")
        self.aplicacion.resultado_var.set(self.prediccion_inicial)
        self.actualizar_telemetria()

    def reiniciar(self):
        self.cancelar_temporizador()
        self.reiniciar_estado()
        self.aplicacion.sonidos.iniciar_lanzamiento()
        self.programar_actualizacion(1)

    def cambiar_pausa(self):
        if self.terminado:
            return
        self.pausado = not self.pausado
        if self.pausado:
            self.cancelar_temporizador()
            self.aplicacion.sonidos.detener_motor()
            self.aplicacion.estado_var.set("PAUSADO")
            self.aplicacion.estado_label.configure(fg=AMARILLO)
            self.aplicacion.boton_pausa.configure(text="REANUDAR")
        else:
            self.aplicacion.sonidos.iniciar_motor()
            self.aplicacion.estado_var.set("EN VUELO")
            self.aplicacion.estado_label.configure(fg="#72e0a7")
            self.aplicacion.boton_pausa.configure(text="PAUSAR")
            self.programar_actualizacion(1)

    def cancelar_temporizador(self):
        if self.after_id is not None:
            self.aplicacion.root.after_cancel(self.after_id)
            self.after_id = None
        if self.efecto_id is not None:
            self.aplicacion.root.after_cancel(self.efecto_id)
            self.efecto_id = None

    def programar_actualizacion(self, demora):
        if (
            self.after_id is None
            and not self.terminado
            and not self.pausado
        ):
            self.after_id = self.aplicacion.root.after(demora, self.actualizar)

    def actualizar_telemetria(self):
        altura = max(0.0, math.hypot(self.x, self.y) - self.radio)
        velocidad = math.hypot(self.vx, self.vy)
        self.aplicacion.tiempo_var.set("{:.1f} h".format(self.tiempo / 3600))
        self.aplicacion.altura_var.set("{:,.0f} km".format(altura / 1000))
        self.aplicacion.altura_maxima_var.set(
            "{:,.0f} km".format(self.altura_maxima / 1000)
        )
        self.aplicacion.velocidad_actual_var.set(
            "{:.2f} km/s".format(velocidad / 1000)
        )
        self.aplicacion.combustible_actual_var.set(
            "{:,.0f} kg".format(self.combustible_restante)
        )
        self.muestras_grafica.append(
            (self.tiempo, altura / 1000, velocidad / 1000)
        )
        if len(self.muestras_grafica) > 600:
            self.muestras_grafica.pop(0)
        self._actualizar_grafica()

    def _actualizar_grafica(self):
        grafica = self.aplicacion.grafica_canvas
        grafica.delete("all")
        ancho = max(300, grafica.winfo_width())
        alto = max(130, grafica.winfo_height())
        izquierda = 105
        derecha = ancho - 20
        zonas = (
            (14, alto // 2 - 7, 1, "#f3a55b", "ALTITUD", "km"),
            (alto // 2 + 5, alto - 24, 2, ACENTO, "VELOCIDAD", "km/s"),
        )
        for arriba, abajo, indice, color, titulo, unidad in zonas:
            grafica.create_text(
                14,
                (arriba + abajo) / 2,
                text=titulo,
                fill=TEXTO_SECUNDARIO,
                anchor="w",
                font=("Segoe UI", 8, "bold"),
            )
            maximo = max(
                (muestra[indice] for muestra in self.muestras_grafica),
                default=1.0,
            )
            maximo = max(maximo, 1.0)
            for linea in range(3):
                y = arriba + (abajo - arriba) * linea / 2
                grafica.create_line(
                    izquierda, y, derecha, y, fill="#22364f", dash=(2, 4)
                )
            grafica.create_text(
                derecha,
                arriba,
                text="{:.1f} {}".format(maximo, unidad),
                fill=TEXTO_SECUNDARIO,
                anchor="ne",
                font=("Segoe UI", 7),
            )
            if len(self.muestras_grafica) > 1:
                puntos = []
                cantidad = len(self.muestras_grafica)
                for posicion, muestra in enumerate(self.muestras_grafica):
                    x = izquierda + (derecha - izquierda) * posicion / (
                        cantidad - 1
                    )
                    y = abajo - (abajo - arriba) * muestra[indice] / maximo
                    puntos.extend((x, y))
                grafica.create_line(
                    puntos, fill=color, width=2, smooth=True
                )
        grafica.create_text(
            izquierda,
            alto - 7,
            text="Tiempo simulado: {:.1f} h".format(self.tiempo / 3600),
            fill=TEXTO_SECUNDARIO,
            anchor="sw",
            font=("Segoe UI", 7),
        )

    def terminar(self, mensaje, color, efecto=None):
        if self.terminado:
            return
        self.terminado = True
        self.cancelar_temporizador()
        colores = {
            "red": "#ff7187",
            "yellow": AMARILLO,
            "lime": "#72e0a7",
        }
        self.aplicacion.estado_var.set("SIMULACION FINALIZADA")
        self.aplicacion.estado_label.configure(
            fg=colores.get(color, "#ff7187")
        )
        self.aplicacion.resultado_var.set(mensaje)
        self.actualizar_telemetria()
        self.aplicacion.boton_pausa.configure(state="disabled")
        if not self.mision_guardada:
            self.mision_guardada = True
            self.aplicacion._guardar_mision(self, mensaje)
        if efecto == "celebracion":
            self.aplicacion.sonidos.celebrar()
            self._animar_celebracion()
        elif efecto == "explosion":
            self.aplicacion.sonidos.explosion()
            self._animar_explosion()
        else:
            self.aplicacion.sonidos.detener_motor()

    def _animar_celebracion(self):
        centro_x, centro_y = self._posicion_visual()
        colores = (AMARILLO, ACENTO, "#ffffff", "#ff9b72", "#72e0a7")
        generador = random.Random(self.pasos)
        anillos = []
        for color in (AMARILLO, "#ffffff"):
            anillos.append(
                self.canvas.create_oval(
                    centro_x - 2,
                    centro_y - 2,
                    centro_x + 2,
                    centro_y + 2,
                    outline=color,
                    width=2,
                    tags=("celebracion", "celebracion_animada"),
                )
            )
        particulas = []
        for _ in range(38):
            angulo = generador.uniform(0, 2 * math.pi)
            rapidez = generador.uniform(2.0, 6.0)
            radio = generador.choice((2, 3, 4))
            color = generador.choice(colores)
            objeto = self.canvas.create_oval(
                centro_x - radio,
                centro_y - radio,
                centro_x + radio,
                centro_y + radio,
                fill=color,
                outline="",
                tags=("celebracion", "celebracion_animada"),
            )
            particulas.append((objeto, angulo, rapidez, radio))
        centro_banner = self.ancho_canvas / 2
        banner_y = self.alto_canvas - 47
        self.canvas.create_rectangle(
            centro_banner - 145,
            banner_y - 19,
            centro_banner + 145,
            banner_y + 19,
            fill="#10263a",
            outline=ACENTO,
            width=2,
            tags="celebracion",
        )
        self.canvas.create_text(
            centro_banner,
            banner_y,
            text="ESCAPE CONFIRMADO - MISION CUMPLIDA",
            fill=AMARILLO,
            font=("Segoe UI", 10, "bold"),
            tags="celebracion",
        )

        def animar_cuadro(cuadro):
            if cuadro >= 23:
                self.canvas.delete("celebracion_animada")
                self.efecto_id = None
                return
            radio_anillo = 7 + cuadro * 6
            self.canvas.coords(
                anillos[0],
                centro_x - radio_anillo,
                centro_y - radio_anillo,
                centro_x + radio_anillo,
                centro_y + radio_anillo,
            )
            radio_anillo_secundario = max(2, radio_anillo - 35)
            self.canvas.coords(
                anillos[1],
                centro_x - radio_anillo_secundario,
                centro_y - radio_anillo_secundario,
                centro_x + radio_anillo_secundario,
                centro_y + radio_anillo_secundario,
            )
            for objeto, angulo, rapidez, radio in particulas:
                distancia = rapidez * cuadro
                x = centro_x + math.cos(angulo) * distancia
                y = (
                    centro_y
                    + math.sin(angulo) * distancia
                    + 0.12 * cuadro * cuadro
                )
                radio_actual = max(1, radio * (1 - cuadro / 26))
                self.canvas.coords(
                    objeto,
                    x - radio_actual,
                    y - radio_actual,
                    x + radio_actual,
                    y + radio_actual,
                )
            self.efecto_id = self.aplicacion.root.after(
                40, lambda: animar_cuadro(cuadro + 1)
            )

        self.efecto_id = self.aplicacion.root.after(
            1, lambda: animar_cuadro(0)
        )

    def _animar_explosion(self):
        centro_x, centro_y = self._posicion_visual()
        self.canvas.delete("rocket")
        anillo = self.canvas.create_oval(
            centro_x - 3,
            centro_y - 3,
            centro_x + 3,
            centro_y + 3,
            outline="#fff0a8",
            width=3,
            tags=("explosion", "explosion_animada"),
        )
        bola = self.canvas.create_oval(
            centro_x - 3,
            centro_y - 3,
            centro_x + 3,
            centro_y + 3,
            fill="#fff0a8",
            outline="",
            tags=("explosion", "explosion_animada"),
        )
        generador = random.Random(self.pasos + 7)
        particulas = []
        colores_particulas = ("#fff0a8", "#ffbb55", "#ff744f", "#e34f42")
        for _ in range(30):
            angulo = generador.uniform(0, 2 * math.pi)
            rapidez = generador.uniform(1.5, 6.5)
            radio = generador.choice((2, 3, 4, 5))
            color = generador.choice(colores_particulas)
            objeto = self.canvas.create_oval(
                centro_x - radio,
                centro_y - radio,
                centro_x + radio,
                centro_y + radio,
                fill=color,
                outline="",
                tags=("explosion", "explosion_animada"),
            )
            particulas.append((objeto, angulo, rapidez, radio))
        centro_banner = self.ancho_canvas / 2
        banner_y = self.alto_canvas - 47
        self.canvas.create_rectangle(
            centro_banner - 95,
            banner_y - 19,
            centro_banner + 95,
            banner_y + 19,
            fill="#301b24",
            outline="#ff744f",
            width=2,
            tags="explosion",
        )
        self.canvas.create_text(
            centro_banner,
            banner_y,
            text="IMPACTO",
            fill="#ffbb55",
            font=("Segoe UI", 11, "bold"),
            tags="explosion",
        )
        colores_bola = (
            "#fff0a8",
            "#ffcf70",
            "#ff934f",
            "#df583f",
            "#683b39",
        )

        def animar_cuadro(cuadro):
            if cuadro >= 22:
                self.canvas.delete("explosion_animada")
                self.efecto_id = None
                return
            radio_anillo = 5 + cuadro * 7
            self.canvas.coords(
                anillo,
                centro_x - radio_anillo,
                centro_y - radio_anillo,
                centro_x + radio_anillo,
                centro_y + radio_anillo,
            )
            radio_bola = max(2, 20 - cuadro)
            self.canvas.coords(
                bola,
                centro_x - radio_bola,
                centro_y - radio_bola,
                centro_x + radio_bola,
                centro_y + radio_bola,
            )
            self.canvas.itemconfigure(
                bola, fill=colores_bola[min(cuadro // 5, len(colores_bola) - 1)]
            )
            for objeto, angulo, rapidez, radio in particulas:
                distancia = rapidez * cuadro
                x = centro_x + math.cos(angulo) * distancia
                y = (
                    centro_y
                    + math.sin(angulo) * distancia
                    + 0.2 * cuadro * cuadro
                )
                radio_actual = max(1, radio * (1 - cuadro / 25))
                self.canvas.coords(
                    objeto,
                    x - radio_actual,
                    y - radio_actual,
                    x + radio_actual,
                    y + radio_actual,
                )
            self.efecto_id = self.aplicacion.root.after(
                40, lambda: animar_cuadro(cuadro + 1)
            )

        self.efecto_id = self.aplicacion.root.after(
            1, lambda: animar_cuadro(0)
        )

    def actualizar(self):
        self.after_id = None
        if self.terminado or self.pausado:
            return

        cantidad_pasos = PASOS_POR_CUADRO * int(
            self.aplicacion.velocidad_simulacion_var.get()
        )
        for _ in range(cantidad_pasos):
            posicion_anterior = self._posicion_visual()
            try:
                (
                    self.x,
                    self.y,
                    self.vx,
                    self.vy,
                    self.combustible_restante,
                ) = paso_fisico(
                    self.x,
                    self.y,
                    self.vx,
                    self.vy,
                    self.combustible_restante,
                    self.planeta,
                    self.cohete,
                    self.mu,
                    self.radio,
                    self.dt,
                )
            except ValueError as error:
                self.terminar(
                    "Simulacion detenida: {}".format(error), "red"
                )
                return
            self.pasos += 1
            self.tiempo += self.dt
            self.trayectoria.append((self.x, self.y))
            radio = math.hypot(self.x, self.y)
            altura = radio - self.radio
            self.altura_maxima = max(self.altura_maxima, altura)
            posicion_actual = self._posicion_visual()
            self.canvas.create_line(
                posicion_anterior[0],
                posicion_anterior[1],
                posicion_actual[0],
                posicion_actual[1],
                fill="#f3a55b",
                width=2,
                tags="trajectory",
            )
            self._dibujar_cohete()

            if radio <= self.radio:
                self.terminar(
                    "Impacto contra el planeta.", "red", efecto="explosion"
                )
                return

            if radio >= RADIO_VISUAL_MAXIMO * self.radio:
                energia_actual = energia_especifica(
                    self.vx, self.vy, radio, self.mu
                )
                if energia_actual >= 0:
                    self.terminar(
                        "El cohete escapo del planeta.",
                        "lime",
                        efecto="celebracion",
                    )
                else:
                    self.terminar(
                        "Trayectoria ligada; salio del area visual.", "yellow"
                    )
                return

            if (
                self.pasos >= MAX_PASOS_SIMULACION
                or self.tiempo >= MAX_TIEMPO_SIMULACION
            ):
                energia_actual = energia_especifica(
                    self.vx, self.vy, radio, self.mu
                )
                if energia_actual >= 0:
                    mensaje, color = (
                        "Escape previsto por energia; se alcanzo el limite.",
                        "lime",
                    )
                else:
                    mensaje, color = (
                        "Trayectoria ligada; se alcanzo el limite.",
                        "yellow",
                    )
                self.terminar(mensaje, color)
                return

        self.actualizar_telemetria()
        self.programar_actualizacion(PAUSA_ANIMACION_MS)


def main():
    Aplicacion().ejecutar()


if __name__ == "__main__":
    main()

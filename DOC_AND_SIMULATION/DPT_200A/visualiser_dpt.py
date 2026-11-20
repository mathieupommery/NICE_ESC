from pathlib import Path
import csv
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.widgets import CheckButtons, Button, TextBox

ROOT = Path(__file__).resolve().parent
NAMES = ['Onduleur V1', 'Onduleur v2', 'Simulation LTspice']
CURRENT_GAINS = [-200., 200.]  # A/V : polarités corrigées des deux acquisitions
CHANNELS = ['CH1 Vgs [V]', 'CH2 I inductance [A]', 'CH3 Vds [V]', 'CH4 bus [V]']
COLORS = ['#C62828', '#8B4513', '#0057B8', '#455A64',
          '#00733E', '#006B68', '#B34D00', '#703D57',
          '#111111', '#555555', '#7030A0', '#39456B']
DEFAULT_SIM_OFFSET_US = 41.25
BACKGROUND = '#ffffff'
PANEL = '#ffffff'
TEXT = '#202020'
plt.rcParams.update({
    'figure.facecolor': BACKGROUND, 'axes.facecolor': '#ffffff',
    'savefig.facecolor': BACKGROUND, 'text.color': TEXT,
    'axes.labelcolor': TEXT, 'xtick.color': '#404040', 'ytick.color': '#404040',
    'axes.edgecolor': '#304256', 'grid.color': '#d0d0d0',
    'font.family': 'DejaVu Sans', 'font.size': 10,
    'axes.titleweight': 'bold', 'legend.facecolor': PANEL,
    'legend.edgecolor': '#304256', 'legend.labelcolor': TEXT,
})


def load(filename):
    path = ROOT / filename
    with path.open(encoding='utf-8-sig') as stream:
        for skip, line in enumerate(stream):
            if line.strip().upper().startswith('TIME,'):
                break
        else:
            raise ValueError(f'Entete TIME introuvable : {path}')
    print(f'Chargement de {filename}...', flush=True)
    frame = pd.read_csv(path, skiprows=skip, usecols=['TIME', 'CH1', 'CH2', 'CH3', 'CH4'],
                        dtype={'TIME': 'float64', **{f'CH{i}': 'float32' for i in range(1, 5)}})
    t = frame['TIME'].to_numpy(copy=True) * 1e6
    y = frame[['CH1', 'CH2', 'CH3', 'CH4']].to_numpy(copy=True)
    if len(t) < 2 or not np.isfinite(t).all() or not np.all(np.diff(t) > 0):
        raise ValueError(f'Axe temporel invalide : {filename}')
    for ch in range(4):
        invalid = ~np.isfinite(y[:, ch])
        count = int(invalid.sum())
        if count:
            print(f'  CH{ch + 1} : {count} points non finis (inf/NaN), affiches comme des lacunes.', flush=True)
            y[invalid, ch] = np.nan
    return t, y


def envelope(t, y, left, right, budget=4000):
    a = max(0, np.searchsorted(t, left) - 1)
    b = min(len(t), np.searchsorted(t, right, side='right') + 1)
    n = b - a
    if n <= budget:
        return t[a:b], y[a:b]
    width = max(1, int(np.ceil(n / (budget // 2))))
    blocks = n // width
    values = y[a:a + blocks * width].reshape(blocks, width)
    starts = a + np.arange(blocks) * width
    indices = np.sort(np.concatenate((starts + np.where(np.isfinite(values), values, np.inf).argmin(axis=1),
                                     starts + np.where(np.isfinite(values), values, -np.inf).argmax(axis=1), [a, b - 1])))
    gaps = ~np.isfinite(values)
    affected = gaps.any(axis=1)
    indices = np.unique(np.concatenate((indices, starts[affected] + gaps[affected].argmax(axis=1))))
    return t[indices], y[indices]


def load_ltspice_table(path):
    raw = Path(path).read_bytes()
    encoding = 'utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
    try:
        text = raw.decode(encoding)
    except UnicodeDecodeError:
        text = raw.decode('cp1252')
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError('Export LTspice vide.')
    if any(line.lower().startswith('step information:') for line in lines):
        raise ValueError('Export avec .step : exporter une seule simulation à la fois.')
    header = lines[0]
    sep = '\t' if '\t' in header else ';' if ';' in header else ','
    import io
    frame = pd.read_csv(io.StringIO('\n'.join(lines)), sep=sep)
    frame.columns = [str(c).strip() for c in frame.columns]
    if frame.columns.duplicated().any() or len(frame.columns) < 4:
        raise ValueError('Exporter Time et les trois traces Vgs, IL et Vds.')
    return frame


def simulation_arrays(frame, time_col, vgs_col, il_col, vds_col):
    columns = [time_col, vgs_col, il_col, vds_col]
    if len(set(columns)) != 4:
        raise ValueError('Sélectionner quatre colonnes distinctes.')
    numeric = frame[columns].apply(lambda col: pd.to_numeric(
        col.astype(str).str.replace(',', '.', regex=False), errors='raise'))
    array = numeric.to_numpy(dtype=float)
    if len(array) < 2 or not np.isfinite(array).all():
        raise ValueError('Simulation : au moins deux points finis requis.')
    if np.any(np.diff(array[:, 0]) <= 0):
        raise ValueError('Temps non croissant : exporter une seule analyse transitoire sans .step.')
    return array[:, 0] * 1e6, np.column_stack((array[:, 1:], np.full(len(array), np.nan)))


def choose_simulation():
    import tkinter as tk
    from tkinter import filedialog, ttk, messagebox
    root = tk.Tk()
    root.withdraw()
    result = []
    try:
        path = filedialog.askopenfilename(parent=root, title='Export transitoire LTspice',
            initialdir=ROOT, filetypes=[('Export LTspice', '*.txt *.csv *.tsv'), ('Tous', '*.*')])
        if not path:
            return None
        frame = load_ltspice_table(path)
        columns = list(frame.columns)
        dialog = tk.Toplevel(root)
        dialog.title('Associer les traces LTspice')
        selections = []
        hints = [('time',), ('vgs',), ('il', 'i(loadind)', 'i(l1)'), ('vds',)]
        for row, (label, aliases) in enumerate(zip(('Temps [s]', 'Vgs [V]', 'IL [A]', 'Vds [V]'), hints)):
            ttk.Label(dialog, text=label).grid(row=row, column=0, padx=12, pady=8)
            default = next((c for c in columns if c.lower() in aliases), columns[min(row, len(columns)-1)])
            box = ttk.Combobox(dialog, values=columns, state='readonly', width=35)
            box.set(default)
            box.grid(row=row, column=1, padx=12, pady=8)
            selections.append(box)
        ttk.Label(dialog, text='Temps en secondes ; courant déjà en ampères.\nChoisir les tensions différentielles grille-source et drain-source.').grid(
            row=4, column=0, columnspan=2, padx=12, pady=10)
        def accept():
            try:
                t, y = simulation_arrays(frame, *(box.get() for box in selections))
                result.append((t, y, Path(path).name))
                dialog.destroy()
            except (ValueError, KeyError) as exc:
                messagebox.showerror('Import impossible', str(exc), parent=dialog)
        ttk.Button(dialog, text='Superposer', command=accept).grid(row=5, column=0, columnspan=2, pady=12)
        dialog.grab_set()
        root.wait_window(dialog)
        return result[0] if result else None
    finally:
        root.destroy()


def rising_time_10_90(t, y, low, high):
    if not np.isfinite([low, high]).all() or high <= low:
        raise ValueError('Niveaux de référence invalides.')
    y10, y90 = low + .1 * (high-low), low + .9 * (high-low)
    start = None
    events = []
    for j in range(1, len(t)):
        a, b = y[j-1], y[j]
        if not np.isfinite([a, b]).all():
            start = None
            continue
        if a < y10 <= b:
            start = t[j-1] + (y10-a) / (b-a) * (t[j]-t[j-1])
        if start is not None and a < y90 <= b:
            stop = t[j-1] + (y90-a) / (b-a) * (t[j]-t[j-1])
            events.append((float(start), float(stop), float(y10), float(y90)))
            start = None
        elif b < y10:
            start = None
    if len(events) != 1:
        raise ValueError('Zoomer sur un seul front montant complet Vds V2 (10–90 %). '
                         f'{len(events)} front(s) détecté(s).')
    return events[0]


class Viewer:
    def __init__(self):
        self.data = [load('tek0001.csv'), load('tek0000.csv')]
        self.offsets = [0., 0., DEFAULT_SIM_OFFSET_US]
        self.sim_loaded = False
        self.zeros = []
        self.current = []
        self.points = []
        self.artists = []
        self.rise_measurement = None
        self.rise_artists = []
        self.fall_measurement = None
        self.fall_artists = []
        self.active = [True, False, True, False] * 2 + [False] * 4
        self.measuring = False
        self.drag = None
        self.manual_y = False
        self.pending_redraw = False
        self.levels = {}
        self.ranges = {}
        self.fig, self.ax = plt.subplots(figsize=(17, 9))
        self.fig.canvas.manager.set_window_title('DPT Studio | Comparaison onduleurs')
        self.fig.subplots_adjust(left=.08, right=.73, bottom=.21, top=.93)
        self.amp = self.ax.twinx()
        self.ax.set(xlabel='Temps [µs] (decalage par acquisition possible)', ylabel='Tensions [V]')
        self.amp.set_ylabel('Courant inductance [A]')
        self.ax.grid(alpha=.45, linewidth=.6)
        self.fig.suptitle('Comparaison des test à double impulsion et simulation LTspice', x=.08, ha='left', fontsize=15, fontweight='bold')
        self.fig.text(.78, .92, 'SIGNAUX', color='#53c7ff', fontsize=11, weight='bold')
        self.status = self.fig.text(.08, .025, '', fontsize=9)
        self.widgets = []
        self.lines = []
        for k, (t, y) in enumerate(self.data):
            end = t[max(1, len(t) // 20) - 1]
            baseline = y[:max(1, len(t) // 20), 1]
            valid = baseline[np.isfinite(baseline)]
            if not len(valid):
                raise ValueError(f'{NAMES[k]} : aucun point CH2 valide dans le repos initial')
            self.zeros.append(float(valid.mean(dtype=np.float64)))
            self.current.append((y[:, 1] - self.zeros[k]) * CURRENT_GAINS[k])
            for ch in range(4):
                axis = self.amp if ch == 1 else self.ax
                line, = axis.plot([], [], color=COLORS[k * 4 + ch], ls='-' if k == 0 else '--',
                                  lw=1, label=f'{NAMES[k]} / {CHANNELS[ch]}',
                                  visible=self.active[k * 4 + ch])
                self.lines.append(line)
        self.data.append((np.array([0., 1.]), np.full((2, 4), np.nan)))
        self.zeros.append(0.)
        self.current.append(np.full(2, np.nan))
        for ch in range(4):
            axis = self.amp if ch == 1 else self.ax
            line, = axis.plot([], [], color=COLORS[8 + ch], ls=':', lw=1.8,
                              label=f'LTspice / {CHANNELS[ch]}', visible=False)
            self.lines.append(line)
        self.fig.text(.78, .345, 'BORNES Vds V2 [µs]', color=TEXT, fontsize=10, weight='bold')
        self.bound10 = TextBox(self.fig.add_axes([.87, .30, .11, .03]),
                               'Montée début ', initial='135.347', color=PANEL)
        self.bound90 = TextBox(self.fig.add_axes([.87, .255, .11, .03]),
                               'Montée fin ', initial='135.38', color=PANEL)
        self.fall90 = TextBox(self.fig.add_axes([.87, .21, .11, .03]),
                              'Descente début ', initial='137.298', color=PANEL)
        self.fall10 = TextBox(self.fig.add_axes([.87, .165, .11, .03]),
                              'Descente fin ', initial='137.399', color=PANEL)
        for box in (self.bound10, self.bound90, self.fall90, self.fall10):
            box.label.set_fontsize(8)
            box.text_disp.set_color(TEXT)
        self.fig.text(.78, .125, 'Bornes manuelles : saisir puis appliquer.', fontsize=8, color=TEXT)
        labels = [f'{"Nouveau" if k == 0 else "Ancien"} : {ch}' for k in range(2) for ch in CHANNELS]
        self.check = CheckButtons(self.fig.add_axes([.78, .51, .21, .38]), labels + ['LTspice : Vgs [V]', 'LTspice : IL [A]', 'LTspice : Vds [V]'], self.active[:11])
        self.check.ax.set_facecolor(PANEL)
        for i, label in enumerate(self.check.labels):
            label.set_color(COLORS[i])
            label.set_fontsize(8)
        if hasattr(self.check, 'set_check_props'):
            self.check.set_check_props({'color': ['#53c7ff'] * 11})
        labels += ['LTspice : Vgs [V]', 'LTspice : IL [A]', 'LTspice : Vds [V]']
        self.check.on_clicked(lambda label: self.toggle(labels.index(label)))
        self.edge_check = CheckButtons(self.fig.add_axes([.78, .425, .21, .075]),
                                       ['Toff : extinction (Vds monte)',
                                        'Ton : allumage (Vds descend)'], [True, False])
        for label in self.edge_check.labels:
            label.set_fontsize(8)
            label.set_color('#B71C1C')
        self.edge_check.on_clicked(self.update_edge_visibility)
        self.button('Appliquer bornes / décalage', [.78, .08, .21, .04], self.apply)
        self.button('Vue initiale', [.08, .12, .12, .045], self.fit)
        self.measure_button = self.button('Marqueurs : OFF', [.21, .12, .14, .045], self.measure)
        self.button('Effacer', [.36, .12, .10, .045], self.clear)
        self.button('Mesures CSV', [.47, .12, .12, .045], self.export)
        self.button('Capture PNG', [.60, .12, .13, .045], self.capture)
        self.sim_offset = TextBox(self.fig.add_axes([.23, .065, .10, .03]),
                                  'Décalage LTspice [µs] ', initial=f'{DEFAULT_SIM_OFFSET_US:g}', color=PANEL)
        self.sim_sign = TextBox(self.fig.add_axes([.47, .065, .07, .03]),
                                'Signe IL simu (+1/-1) ', initial='1', color=PANEL)
        for box in (self.sim_offset, self.sim_sign):
            box.label.set_fontsize(8)
            box.text_disp.set_color(TEXT)
        self.button('Auto montée', [.78, .38, .10, .035], self.measure_rise)
        self.button('Auto descente', [.89, .38, .10, .035], self.measure_fall)
        self.button('Importer LTspice', [.60, .06, .13, .04], self.import_simulation)
        self.build_levels()
        self.timer = self.fig.canvas.new_timer(interval=33)
        self.timer.add_callback(self.flush_redraw)
        self.timer.start()
        self.ax.callbacks.connect('xlim_changed', self.request_redraw)
        self.fig.canvas.mpl_connect('resize_event', self.request_redraw)
        self.fig.canvas.mpl_connect('button_press_event', self.click)
        self.fig.canvas.mpl_connect('motion_notify_event', self.pan_move)
        self.fig.canvas.mpl_connect('button_release_event', self.pan_end)
        self.fig.canvas.mpl_connect('scroll_event', self.wheel)
        self.fit()
        self.update_legend()
        self.apply()

    def import_simulation(self, *_):
        try:
            result = choose_simulation()
            if result is None:
                return
            t, y, name = result
            sign = float(self.sim_sign.text)
            offset = float(self.sim_offset.text)
            if sign not in (-1., 1.) or not np.isfinite(offset):
                raise ValueError('Vérifier le signe et le décalage LTspice.')
            self.data[2] = (t, y)
            self.current[2] = y[:, 1] * sign
            self.offsets[2] = offset
            self.sim_loaded = True
            self.clear()
            self.build_levels()
            for i in (8, 10):  # Vgs et Vds uniquement ; IL reste masqué par défaut.
                if not self.active[i]:
                    self.check.set_active(i)
            self.fit()
            self.update_legend()
            self.status.set_text(f'LTspice : {name}. Régler le décalage puis Appliquer bornes / décalage.')
        except Exception as exc:
            self.status.set_text(f'Import LTspice impossible : {exc}')
        self.fig.canvas.draw_idle()

    def measure_fall(self, *_):
        self.measure_rise(falling=True)

    def measure_rise(self, *_, falling=False):
        try:
            if not self.active[6]:
                raise ValueError('Activer Vds V2 avant de mesurer son front.')
            t, data = self.data[1]
            displayed_t = t + self.offsets[1]
            left, right = self.ax.get_xlim()
            selected = (displayed_t >= left) & (displayed_t <= right)
            bus = data[selected, 3]
            bus = bus[np.isfinite(bus)]
            if len(bus) < 2:
                raise ValueError('Pas assez de points de bus V2 dans la fenêtre visible.')
            high = float(np.median(bus))
            signal = data[selected, 2]
            result = rising_time_10_90(displayed_t[selected], -signal if falling else signal,
                                       -high if falling else 0., 0. if falling else high)
            if falling:
                result = (*result[:2], -result[2], -result[3])
            prefix = 'fall' if falling else 'rise'
            for artist in getattr(self, prefix + '_artists'):
                artist.remove()
            boxes = (self.fall90, self.fall10) if falling else (self.bound10, self.bound90)
            for box, value in zip(boxes, result[:2]):
                box.set_val(f'{value:.10g}')
            setattr(self, prefix + '_measurement', result)
            setattr(self, prefix + '_artists', self.draw_rise(self.ax, result, falling))
            self.update_edge_visibility()
            self.status.set_text(f'{"Descente" if falling else "Montée"} Vds V2 : '
                                 f'{(result[1]-result[0])*1000:.1f} ns ; références 0 V / bus {high:.3g} V.')
        except ValueError as exc:
            self.status.set_text(str(exc))
        self.fig.canvas.draw_idle()

    @staticmethod
    def draw_rise(axis, result, falling=False):
        t10, t90, y10, y90 = result
        red = '#B71C1C'
        lines = [axis.axvline(t, color=red, ls=':', lw=1.3, zorder=7)
                 for t in (t10, t90)]
        points, = axis.plot([t10, t90], [y10, y90], 'o', color=red, ms=4, zorder=8)
        for t, y in ((t10, y10), (t90, y90)):
            lines.append(axis.annotate('', xy=(t, y), xycoords='data',
                         xytext=(0, y), textcoords=axis.get_yaxis_transform(),
                         annotation_clip=True, zorder=7,
                         arrowprops=dict(arrowstyle='-', color=red, linestyle=':',
                                         linewidth=1.3, shrinkA=0, shrinkB=0)))
            lines.append(axis.annotate(f'{y:.2f} V', xy=(t, y), xycoords='data',
                         xytext=(-.006, y), textcoords=axis.get_yaxis_transform(),
                         annotation_clip=True, ha='right', va='center', color=red,
                         fontsize=8, bbox=dict(fc='white', ec='none', pad=1), zorder=11))
        title = 'Ton — allumage' if falling else 'Toff — extinction'
        label = axis.annotate(f'{title} Vds V2\nΔt : {(t90-t10)*1000:.1f} ns',
                              (t10, (y10+y90)/2), xytext=(-12, 0),
                              textcoords='offset points', ha='right', va='center',
                              color=red, fontsize=10, fontweight='bold', zorder=10,
                              bbox=dict(boxstyle='round,pad=.3', fc='white', ec='none', alpha=.9))
        return lines + [points, label]

    def button(self, label, rect, callback):
        widget = Button(self.fig.add_axes(rect), label, color=PANEL, hovercolor='#eeeeee')
        widget.label.set_color(TEXT)
        widget.label.set_fontsize(9)
        widget.on_clicked(callback)
        self.widgets.append(widget)
        return widget

    def values(self, k, ch):
        return self.current[k] if ch == 1 else self.data[k][1][:, ch]

    def build_levels(self):
        print('Preparation des resolutions pour navigation fluide...', flush=True)
        for k in range(len(self.data)):
            for ch in range(4):
                x, y = self.data[k][0], self.values(k, ch)
                self.ranges[k, ch] = ((float(np.nanmin(y)), float(np.nanmax(y)))
                                     if np.isfinite(y).any() else None)
                levels = [(x, y)]
                while len(x) > 8000:
                    x, y = envelope(x, y, x[0], x[-1], max(2000, len(x) // 16))
                    levels.append((x, y))
                self.levels[k, ch] = levels

    def request_redraw(self, *_):
        self.pending_redraw = True

    def flush_redraw(self):
        if self.pending_redraw:
            self.redraw()

    def toolbar_active(self):
        toolbar = getattr(self.fig.canvas.manager, 'toolbar', None)
        return bool(toolbar and toolbar.mode)

    def pan_move(self, event):
        if self.drag is None or event.x is None or event.y is None:
            return
        sx, sy, xlim, ylim, alim, press = self.drag
        dx = (event.x - sx) / self.ax.bbox.width * (xlim[1] - xlim[0])
        self.ax.set_xlim(xlim[0] - dx, xlim[1] - dx)
        if self.manual_y:
            for axis, limits in ((self.ax, ylim), (self.amp, alim)):
                dy = (event.y - sy) / self.ax.bbox.height * (limits[1] - limits[0])
                axis.set_ylim(limits[0] - dy, limits[1] - dy)
        self.request_redraw()

    def pan_end(self, event):
        if self.drag is None:
            return
        sx, sy, _, _, _, press = self.drag
        self.drag = None
        if event.x is not None and event.y is not None and np.hypot(event.x - sx, event.y - sy) < 4:
            self.place_measurement(press)
        self.redraw()

    def wheel(self, event):
        if event.inaxes not in (self.ax, self.amp) or self.toolbar_active():
            return
        factor = 0.8 if event.step > 0 else 1.25
        vertical = 'control' in (event.key or '') or 'ctrl' in (event.key or '')
        if vertical:
            self.manual_y = True
        axes = (self.ax, self.amp) if vertical else (self.ax,)
        fraction = ((event.y - self.ax.bbox.y0) / self.ax.bbox.height if vertical
                    else (event.x - self.ax.bbox.x0) / self.ax.bbox.width)
        for axis in axes:
            low, high = axis.get_ylim() if vertical else axis.get_xlim()
            span = np.clip((high - low) * factor, 1e-9 if vertical else .0004, 1e12)
            anchor = low + fraction * (high - low)
            limits = (anchor - fraction * span, anchor + (1 - fraction) * span)
            (axis.set_ylim if vertical else axis.set_xlim)(limits)
        self.request_redraw()

    def set_current_axis_visibility(self, axis):
        visible = any(self.active[i] for i in range(1, len(self.active), 4))
        axis.yaxis.set_visible(visible)
        axis.spines['right'].set_visible(visible)

    def redraw(self, *_):
        self.set_current_axis_visibility(self.amp)
        self.update_edge_visibility()
        self.pending_redraw = False
        left, right = self.ax.get_xlim()
        for i, line in enumerate(self.lines):
            if self.active[i]:
                k, ch = divmod(i, 4)
                lo, hi = left - self.offsets[k], right - self.offsets[k]
                budget = max(800, int(self.ax.bbox.width * 2))
                x, y = self.levels[k, ch][-1]
                for tx, ty in self.levels[k, ch]:
                    x, y = tx, ty
                    if np.searchsorted(tx, hi) - np.searchsorted(tx, lo) <= budget * 4:
                        break
                x, y = envelope(x, y, lo, hi, budget)
                line.set_data(x + self.offsets[k], y)
        self.fig.canvas.draw_idle()

    def update_edge_visibility(self, *_):
        for enabled, artists in zip(self.edge_check.get_status(),
                                    (self.rise_artists, self.fall_artists)):
            for artist in artists:
                artist.set_visible(bool(enabled and self.active[6]))
        self.fig.canvas.draw_idle()

    def toggle(self, i):
        self.active[i] = not self.active[i]
        self.lines[i].set_visible(self.active[i])
        if i % 4 == 1 and self.active[i] and not self.manual_y:
            ranges = [self.ranges[k, 1] for k in range(len(self.data))
                      if self.active[4 * k + 1] and self.ranges[k, 1] is not None]
            if ranges:
                low, high = min(r[0] for r in ranges), max(r[1] for r in ranges)
                margin = max((high - low) * .05, .01)
                self.amp.set_ylim(low - margin, high + margin)
        self.update_legend()
        self.redraw()

    def update_legend(self):
        old = self.amp.get_legend()
        if old is not None:
            old.remove()
        handles = [line for i, line in enumerate(self.lines) if self.active[i]]
        if handles:
            legend = self.amp.legend(handles=handles, loc='upper right', fontsize=8,
                                     framealpha=.92, ncol=2 if len(handles) > 4 else 1)
            legend.set_zorder(20)
            legend.set_draggable(True)
            for label, line in zip(legend.get_texts(), handles):
                label.set_color(line.get_color())

    def fit(self, *_):
        self.manual_y = False
        self.ax.set_xlim(min(t[0] + self.offsets[k] for k, (t, _) in enumerate(self.data) if k < 2 or self.sim_loaded),
                         max(t[-1] + self.offsets[k] for k, (t, _) in enumerate(self.data) if k < 2 or self.sim_loaded))
        for axis, channels in [(self.ax, (0, 2, 3)), (self.amp, (1,))]:
            ranges = [self.ranges[k, ch] for k in range(len(self.data)) for ch in channels
                      if self.active[k * 4 + ch] and self.ranges[k, ch] is not None]
            if ranges:
                low, high = min(r[0] for r in ranges), max(r[1] for r in ranges)
                margin = max((high - low) * .05, .01)
                axis.set_ylim(low - margin, high + margin)
        self.redraw()

    def apply(self, *_):
        try:
            offset = float(self.sim_offset.text.replace(',', '.'))
            sign = float(self.sim_sign.text)
            if not np.isfinite(offset) or sign not in (-1., 1.):
                raise ValueError('Décalage fini et signe IL égal à +1 ou -1 requis.')
            results = []
            for boxes in ((self.bound10, self.bound90), (self.fall90, self.fall10)):
                result = None
                if any(box.text.strip() for box in boxes):
                    start, end = [float(box.text.replace(',', '.')) for box in boxes]
                    t, data = self.data[1]
                    displayed = t + self.offsets[1]
                    if not np.isfinite([start, end]).all() or not displayed[0] <= start < end <= displayed[-1]:
                        raise ValueError('Saisir deux bornes croissantes dans l’acquisition V2 pour chaque front.')
                    values = np.interp([start, end], displayed, data[:, 2])
                    if not np.isfinite(values).all():
                        raise ValueError('Vds invalide aux bornes sélectionnées.')
                    result = (start, end, float(values[0]), float(values[1]))
                results.append(result)
            self.offsets[2] = offset
            self.current[2] = self.data[2][1][:, 1] * sign
            self.clear()
            self.build_levels()
            for falling, result in enumerate(results):
                if result is None:
                    continue
                prefix = 'fall' if falling else 'rise'
                boxes = (self.fall90, self.fall10) if falling else (self.bound10, self.bound90)
                for box, value in zip(boxes, result[:2]):
                    box.set_val(f'{value:.10g}')
                setattr(self, prefix + '_measurement', result)
                artists = self.draw_rise(self.ax, result, bool(falling))
                setattr(self, prefix + '_artists', artists)
                for artist in artists:
                    artist.set_visible(self.active[6])
            self.status.set_text('Bornes appliquées : tensions interpolées sur Vds V2. Vérifier les niveaux 10 % et 90 %.')
            self.redraw()
        except ValueError as exc:
            self.status.set_text(str(exc))
        self.fig.canvas.draw_idle()

    def measure(self, *_):
        self.measuring = not self.measuring
        self.measure_button.label.set_text('Marqueurs : ' + ('ON' if self.measuring else 'OFF'))
        self.measure_button.label.set_color('#59e3b0' if self.measuring else TEXT)
        self.fig.canvas.draw_idle()

    def click(self, event):
        if event.inaxes not in (self.ax, self.amp) or event.button != 1 or self.toolbar_active():
            return
        if event.dblclick:
            self.drag = None
            self.fit()
            return
        self.drag = (event.x, event.y, self.ax.get_xlim(), self.ax.get_ylim(), self.amp.get_ylim(), event)

    def place_measurement(self, event):
        toolbar = getattr(self.fig.canvas.manager, 'toolbar', None)
        if not self.measuring or event.inaxes not in (self.ax, self.amp) or event.button != 1 or (toolbar and toolbar.mode):
            return
        x = self.ax.transData.inverted().transform((event.x, event.y))[0]
        candidates = []
        for i, line in enumerate(self.lines):
            if not self.active[i]:
                continue
            k, ch = divmod(i, 4)
            t = self.data[k][0]
            index = int(np.clip(np.searchsorted(t, x - self.offsets[k]), 0, len(t) - 1))
            if index and abs(t[index - 1] + self.offsets[k] - x) < abs(t[index] + self.offsets[k] - x):
                index -= 1
            tx, value = t[index] + self.offsets[k], float(self.values(k, ch)[index])
            if not np.isfinite(value):
                continue
            pixel = line.axes.transData.transform((tx, value))
            candidates.append((np.hypot(pixel[0] - event.x, pixel[1] - event.y), i, index, tx, value))
        if not candidates:
            return
        _, i, index, tx, value = min(candidates)
        k, ch = divmod(i, 4)
        unit = 'A' if ch == 1 else 'V'
        previous = next((p for p in reversed(self.points) if p[0] == i), None)
        self.points.append((i, index, tx, value))
        axis = self.lines[i].axes
        self.artists.extend(self.draw_marker(axis, len(self.points), i, tx, value, previous))
        message = f'P{len(self.points)} — {NAMES[k]} / {CHANNELS[ch]} : {tx:.9g} µs ; {value:.9g} {unit}'
        if previous:
            message += f' ; delta t = {tx - previous[2]:.9g} µs ; delta y = {value - previous[3]:.9g} {unit}'
        self.status.set_text(message)
        self.fig.canvas.draw_idle()

    @staticmethod
    def draw_marker(axis, number, i, tx, value, previous=None):
        k, ch = divmod(i, 4)
        color = COLORS[i]
        unit = 'A' if ch == 1 else 'V'
        marker, = axis.plot(tx, value, 'o', color=color, markeredgecolor='white', ms=6, zorder=8)
        vertical = axis.axvline(tx, color=color, ls=':', lw=.8, alpha=.65)
        horizontal = axis.axhline(value, color=color, ls=':', lw=.8, alpha=.65)
        caption = f'P{number} · {NAMES[k]} CH{ch + 1}\n{tx:.7g} µs | {value:.7g} {unit}'
        if previous:
            caption += f'\nΔt {tx - previous[2]:.6g} µs | Δ {value - previous[3]:.6g} {unit}'
        # Keep labels inward when the marker is close to the plot boundary.
        fraction = (tx - axis.get_xlim()[0]) / (axis.get_xlim()[1] - axis.get_xlim()[0])
        upper = value > sum(axis.get_ylim()) / 2
        label = axis.annotate(caption, (tx, value),
                              xytext=(-12 if fraction > .65 else 12, -18 if upper else 18),
                              ha='right' if fraction > .65 else 'left', va='top' if upper else 'bottom',
                              textcoords='offset points', fontsize=8, color=TEXT, zorder=9,
                              bbox=dict(boxstyle='round,pad=.45', fc=PANEL, ec=color, alpha=.95),
                              arrowprops=dict(arrowstyle='-', color=color, lw=.8))
        return [marker, vertical, horizontal, label]

    def capture(self, *_):
        from datetime import datetime
        path = ROOT / ('capture_dpt_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.png')
        shot = None
        try:
            self.redraw()
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            shot = Figure(figsize=(14, 8), facecolor=BACKGROUND)
            FigureCanvasAgg(shot)
            voltage = shot.add_subplot(111)
            current = voltage.twinx()
            voltage.set(xlim=self.ax.get_xlim(), ylim=self.ax.get_ylim(),
                        xlabel='Temps [µs]', ylabel='Tensions [V]')
            current.set_ylim(self.amp.get_ylim())
            current.set_ylabel('Courant [A]')
            self.set_current_axis_visibility(current)
            voltage.grid(alpha=.45, linewidth=.6)
            handles = []
            for i, source in enumerate(self.lines):
                if self.active[i]:
                    target = current if i % 4 == 1 else voltage
                    line, = target.plot(*source.get_data(), color=source.get_color(),
                                        ls=source.get_linestyle(), lw=1, label=source.get_label())
                    handles.append(line)
            for number, (i, index, tx, value) in enumerate(self.points, 1):
                if not self.active[i] or not self.ax.get_xlim()[0] <= tx <= self.ax.get_xlim()[1]:
                    continue
                previous = next((p for p in reversed(self.points[:number - 1]) if p[0] == i), None)
                self.draw_marker(current if i % 4 == 1 else voltage, number, i, tx, value, previous)
            if self.rise_measurement is not None and self.active[6] and self.edge_check.get_status()[0]:
                self.draw_rise(voltage, self.rise_measurement)
            if self.fall_measurement is not None and self.active[6] and self.edge_check.get_status()[1]:
                self.draw_rise(voltage, self.fall_measurement, falling=True)
            voltage.set_title('DPT / Mesures et simulation LTspice', loc='left', pad=18)
            if handles:
                legend = current.legend(handles=handles, loc='upper right', framealpha=.92,
                                        ncol=2 if len(handles) > 4 else 1, fontsize=8)
                for label, line in zip(legend.get_texts(), handles):
                    label.set_color(line.get_color())
            shot.subplots_adjust(left=.08, right=.91, bottom=.25, top=.91)
            shot.savefig(path, dpi=180, bbox_inches='tight', facecolor=BACKGROUND)
            self.status.set_text(f'Capture sauvegardee : {path.name}')
        except Exception as exc:
            self.status.set_text(f'Capture impossible : {exc}')
        finally:
            if shot is not None:
                shot.clear()
            self.fig.canvas.draw_idle()

    def clear(self, *_):
        for artist in self.fall_artists:
            artist.remove()
        self.fall_artists.clear()
        self.fall_measurement = None
        self.fall90.set_val('')
        self.fall10.set_val('')
        for artist in self.rise_artists:
            artist.remove()
        self.rise_artists.clear()
        self.rise_measurement = None
        self.bound10.set_val('')
        self.bound90.set_val('')
        for artist in self.artists:
            artist.remove()
        self.artists.clear()
        self.points.clear()
        self.status.set_text('Points effaces.')
        self.fig.canvas.draw_idle()

    def export(self, *_):
        from datetime import datetime
        path = ROOT / ('mesures_dpt_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.csv')
        with path.open('w', newline='', encoding='utf-8-sig') as stream:
            writer = csv.writer(stream, delimiter=';')
            writer.writerow(['Point', 'Acquisition', 'Voie', 'Index', 'Temps_affiche_us', 'Valeur', 'Offset_CH2_V', 'Decalage_us'])
            for number, (i, index, tx, value) in enumerate(self.points, 1):
                k, ch = divmod(i, 4)
                writer.writerow([number, NAMES[k], CHANNELS[ch], index, tx, value, self.zeros[k], self.offsets[k]])
        self.status.set_text(f'Export : {path.name}')
        self.fig.canvas.draw_idle()


if __name__ == '__main__':
    viewer = Viewer()
    plt.show()

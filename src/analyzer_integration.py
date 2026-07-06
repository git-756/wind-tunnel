import sys
import os
import glob
import tempfile
import shutil
import re
import zipfile
import pandas as pd
import numpy as np
import pyqtgraph as pg

# 7zファイル展開用ライブラリのインポート
try:
    import py7zr
    HAS_PY7ZR = True
except ImportError:
    HAS_PY7ZR = False

from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QPushButton, QLabel, QFileDialog, 
                               QMessageBox, QFormLayout, QLineEdit, QGroupBox,
                               QTableWidget, QTableWidgetItem, QHeaderView,
                               QListWidget, QListWidgetItem, QSplitter, QTabWidget,
                               QComboBox, QSpinBox, QCheckBox)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

# グラフ重ね描き用のカラーパレット（最大20色、循環）
COLOR_PALETTE = [
    '#e6194B', '#3cb44b', '#ffe119', '#4363d8', '#f58231',
    '#911eb4', '#42d4f4', '#f032e6', '#bfef45', '#fabed4',
    '#469990', '#dcbeff', '#9a6324', '#fffac8', '#800000',
    '#aaffc3', '#808000', '#ffd8b1', '#000075', '#a9a9a9'
]

class IntegratedWindTunnelAnalyzer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Wind Tunnel Data Analyzer Pro (Integrated Version)")
        self.resize(1500, 950)

        # === 全体比較用データ管理 ===
        # 構造: { "dataset_name": { "df": pd.DataFrame, "color": str, "visible": bool, "pair_map": dict, "temp_dir": str } }
        self.datasets = {}
        self.color_index = 0

        # === 時系列解析用データ管理 ===
        self.ts_df = None
        self.tare_fx_avg = 0.0
        self.tare_fy_avg = 0.0
        self.tare_fz_avg = 0.0
        self.tare_mx_avg = 0.0
        self.tare_my_avg = 0.0
        self.tare_mz_avg = 0.0

        self.setup_ui()
        self.apply_stylesheet()

    def setup_ui(self):
        # メインのタブウィジェット
        self.main_tabs = QTabWidget()
        self.setCentralWidget(self.main_tabs)

        # 各タブの初期化
        self.tab_overall = QWidget()
        self.tab_timeseries = QWidget()

        self.setup_overall_tab()
        self.setup_timeseries_tab()

        self.main_tabs.addTab(self.tab_overall, "📊 全体表示 (複数データセット比較)")
        self.main_tabs.addTab(self.tab_timeseries, "📈 時系列解析 (詳細分析)")

    # =========================================================================
    # タブ1: 全体表示 UI構築
    # =========================================================================
    def setup_overall_tab(self):
        overall_layout = QHBoxLayout(self.tab_overall)
        main_splitter = QSplitter(Qt.Horizontal)
        overall_layout.addWidget(main_splitter)

        # --- 左パネル (コントロールとデータ管理) ---
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_panel.setMinimumWidth(420)
        left_panel.setMaximumWidth(600)

        # 1. パラメータ設定
        group_params = QGroupBox("1. Setup")
        form_params = QFormLayout()
        self.input_area_overall = QLineEdit("0.1875")
        form_params.addRow("Sail Area [m²]:", self.input_area_overall)
        group_params.setLayout(form_params)
        left_layout.addWidget(group_params)

        # 2. データインポート
        group_import = QGroupBox("2. Import Wind Tunnel Data")
        vbox_import = QVBoxLayout()
        
        self.btn_analyze_dir = QPushButton("📁 Load From Folder")
        self.btn_analyze_dir.setStyleSheet("background-color: #28a745; color: white; font-weight: bold; padding: 10px;")
        self.btn_analyze_dir.clicked.connect(self.select_and_analyze_dir)
        vbox_import.addWidget(self.btn_analyze_dir)

        self.btn_analyze_archive = QPushButton("📦 Load From Archive (7z / ZIP)")
        self.btn_analyze_archive.setStyleSheet("background-color: #17a2b8; color: white; font-weight: bold; padding: 10px;")
        self.btn_analyze_archive.clicked.connect(self.select_and_analyze_archive)
        vbox_import.addWidget(self.btn_analyze_archive)

        self.lbl_status_info = QLabel("Filename rule: ..._[Tare/test]_[Angle].csv\nArchives (.7z, .zip) will be extracted automatically.")
        self.lbl_status_info.setStyleSheet("color: #6c757d; font-size: 11px;")
        vbox_import.addWidget(self.lbl_status_info)

        group_import.setLayout(vbox_import)
        left_layout.addWidget(group_import)

        # 3. 読み込み済みデータセット一覧
        group_datasets = QGroupBox("3. Loaded Datasets (Check to Plot)")
        vbox_datasets = QVBoxLayout()
        
        self.dataset_list = QListWidget()
        self.dataset_list.itemChanged.connect(self.on_dataset_visibility_changed)
        self.dataset_list.itemSelectionChanged.connect(self.on_dataset_selection_changed)
        vbox_datasets.addWidget(self.dataset_list)

        hbox_ds_btns = QHBoxLayout()
        self.btn_delete_ds = QPushButton("Delete Selected")
        self.btn_delete_ds.clicked.connect(self.delete_selected_dataset)
        self.btn_clear_all = QPushButton("Clear All")
        self.btn_clear_all.clicked.connect(self.clear_all_datasets)
        hbox_ds_btns.addWidget(self.btn_delete_ds)
        hbox_ds_btns.addWidget(self.btn_clear_all)
        vbox_datasets.addLayout(hbox_ds_btns)

        group_datasets.setLayout(vbox_datasets)
        left_layout.addWidget(group_datasets)

        # 4. 解析結果のテーブル
        group_table = QGroupBox("4. Selected Dataset Details")
        vbox_table = QVBoxLayout()
        
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Angle", "CL", "CD", "L/D"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        vbox_table.addWidget(self.table)

        self.btn_export = QPushButton("💾 Export Selected Summary CSV")
        self.btn_export.setStyleSheet("background-color: #007bff; color: white; font-weight: bold; padding: 8px;")
        self.btn_export.clicked.connect(self.export_csv)
        vbox_table.addWidget(self.btn_export)

        group_table.setLayout(vbox_table)
        left_layout.addWidget(group_table)

        main_splitter.addWidget(left_panel)

        # --- 右パネル (グラフ重ね描き群) ---
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
        pg.setConfigOption('background', 'w')
        pg.setConfigOption('foreground', 'k')
        pg.setConfigOption('antialias', True)

        self.plot_cl = pg.PlotWidget(title="Lift Curve (CL vs Angle)")
        self.plot_cl.showGrid(x=True, y=True)
        self.plot_cl.setLabel('bottom', 'Angle [deg]')
        self.plot_cl.setLabel('left', 'Lift Coefficient (CL)')
        self.plot_cl.addLegend()
        right_layout.addWidget(self.plot_cl)

        self.plot_cd = pg.PlotWidget(title="Drag Curve (CD vs Angle)")
        self.plot_cd.showGrid(x=True, y=True)
        self.plot_cd.setLabel('bottom', 'Angle [deg]')
        self.plot_cd.setLabel('left', 'Drag Coefficient (CD)')
        self.plot_cd.addLegend()
        right_layout.addWidget(self.plot_cd)

        self.plot_polar = pg.PlotWidget(title="Drag Polar (CL vs CD)")
        self.plot_polar.showGrid(x=True, y=True)
        self.plot_polar.setLabel('bottom', 'Drag Coefficient (CD)')
        self.plot_polar.setLabel('left', 'Lift Coefficient (CL)')
        self.plot_polar.addLegend()
        right_layout.addWidget(self.plot_polar)

        main_splitter.addWidget(right_panel)
        main_splitter.setSizes([450, 950])

    # =========================================================================
    # タブ2: 時系列解析 UI構築
    # =========================================================================
    def setup_timeseries_tab(self):
        ts_layout = QHBoxLayout(self.tab_timeseries)
        ts_layout.setContentsMargins(10, 10, 10, 10)
        ts_layout.setSpacing(10)

        splitter = QSplitter(Qt.Horizontal)
        ts_layout.addWidget(splitter)

        # --- 左パネル: コントロール & 統計 ---
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)
        left_panel.setMinimumWidth(380)
        left_panel.setMaximumWidth(450)

        # 1. データ切り替え選択
        group_select = QGroupBox("1. 解析ターゲットデータの選択")
        form_select = QFormLayout()
        
        self.combo_ts_dataset = QComboBox()
        self.combo_ts_dataset.currentIndexChanged.connect(self.on_ts_dataset_changed)
        form_select.addRow("データセット:", self.combo_ts_dataset)

        self.combo_ts_angle = QComboBox()
        self.combo_ts_angle.currentIndexChanged.connect(self.on_ts_angle_changed)
        form_select.addRow("指定角度 [deg]:", self.combo_ts_angle)
        
        group_select.setLayout(form_select)
        left_layout.addWidget(group_select)

        # メタデータ表示部
        group_meta = QGroupBox("データソース情報")
        vbox_meta = QVBoxLayout()
        self.lbl_test_file = QLabel("試験データ未ロード")
        self.lbl_test_file.setWordWrap(True)
        self.lbl_test_file.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        self.lbl_tare_file = QLabel("Tare未適用 (0点オフセット想定)")
        self.lbl_tare_file.setWordWrap(True)
        self.lbl_tare_file.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        self.lbl_meta_memo = QLabel("メモ: -")
        self.lbl_meta_area = QLabel("代表面積: - m²")
        self.lbl_meta_duration = QLabel("測定時間: - 秒")
        self.lbl_meta_rate = QLabel("サンプリング周波数: - Hz")
        for lbl in [self.lbl_meta_memo, self.lbl_meta_area, self.lbl_meta_duration, self.lbl_meta_rate]:
            lbl.setStyleSheet("font-size: 11px; color: #2c3e50; font-weight: bold;")
        vbox_meta.addWidget(QLabel("📄 試験ファイル名:"))
        vbox_meta.addWidget(self.lbl_test_file)
        vbox_meta.addWidget(QLabel("⚖️ Tareファイル名:"))
        vbox_meta.addWidget(self.lbl_tare_file)
        vbox_meta.addWidget(self.lbl_meta_memo)
        vbox_meta.addWidget(self.lbl_meta_area)
        vbox_meta.addWidget(self.lbl_meta_duration)
        vbox_meta.addWidget(self.lbl_meta_rate)
        group_meta.setLayout(vbox_meta)
        left_layout.addWidget(group_meta)

        # 2. 解析設定
        group_settings = QGroupBox("2. 解析・計算設定")
        form_settings = QFormLayout()
        
        self.input_area_ts = QLineEdit("0.1875")
        self.input_area_ts.textChanged.connect(self.recalculate_ts_if_needed)
        form_settings.addRow("代表面積 S [m²]:", self.input_area_ts)

        self.cb_recalc = QCheckBox("Tare (ゼロ点引算) 補正を有効化")
        self.cb_recalc.setChecked(True)
        self.cb_recalc.stateChanged.connect(self.recalculate_ts_if_needed)
        form_settings.addRow(self.cb_recalc)

        self.combo_filter = QComboBox()
        self.combo_filter.addItems(["なし (生データ)", "移動平均 (Moving Average)", "指数移動平均 (EMA)"])
        self.combo_filter.currentIndexChanged.connect(self.apply_filter_and_update_ts)
        form_settings.addRow("ノイズ低減フィルタ:", self.combo_filter)

        self.spin_window = QSpinBox()
        self.spin_window.setRange(1, 500)
        self.spin_window.setValue(10)
        self.spin_window.setSuffix(" pts")
        self.spin_window.valueChanged.connect(self.apply_filter_and_update_ts)
        form_settings.addRow("フィルタ窓幅:", self.spin_window)

        group_settings.setLayout(form_settings)
        left_layout.addWidget(group_settings)

        # 3. 信号解析の対象指定
        group_analysis_var = QGroupBox("3. 信号・周波数解析の対象")
        form_var = QFormLayout()
        self.combo_var = QComboBox()
        self.combo_var.addItems(["CL", "CD", "Fx(N)", "Fy(N)", "Mz(Nm)", "CoP_Z(m)"])
        self.combo_var.currentIndexChanged.connect(self.update_analysis_plots_ts)
        form_var.addRow("ターゲット変数:", self.combo_var)
        group_analysis_var.setLayout(form_var)
        left_layout.addWidget(group_analysis_var)

        # 4. 選択区間の統計レポート
        group_stats = QGroupBox("4. 選択範囲の統計量")
        vbox_stats = QVBoxLayout()
        
        self.lbl_region_info = QLabel("選択範囲: 未選択")
        self.lbl_region_info.setStyleSheet("font-weight: bold; color: #16a085;")
        vbox_stats.addWidget(self.lbl_region_info)

        self.table_stats = QTableWidget(6, 5)
        self.table_stats.setHorizontalHeaderLabels(["変数", "平均値", "標準偏差", "RMS", "最大/最小"])
        self.table_stats.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table_stats.verticalHeader().setVisible(False)
        self.table_stats.setFixedHeight(180)
        vbox_stats.addWidget(self.table_stats)

        hbox_export = QHBoxLayout()
        self.btn_export_region = QPushButton("選択範囲データを保存")
        self.btn_export_region.setStyleSheet("background-color: #3498db; color: white; font-weight: bold;")
        self.btn_export_region.clicked.connect(self.export_region_data)
        self.btn_export_stats = QPushButton("統計サマリーを保存")
        self.btn_export_stats.setStyleSheet("background-color: #2ecc71; color: white; font-weight: bold;")
        self.btn_export_stats.clicked.connect(self.export_stats_summary)
        hbox_export.addWidget(self.btn_export_region)
        hbox_export.addWidget(self.btn_export_stats)
        vbox_stats.addLayout(hbox_export)

        group_stats.setLayout(vbox_stats)
        left_layout.addWidget(group_stats)
        
        left_layout.addStretch()
        splitter.addWidget(left_panel)

        # --- 右パネル: 多機能タブ付きグラフ ---
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.ts_tabs = QTabWidget()
        right_layout.addWidget(self.ts_tabs)

        # タブ2-1: 空力性能
        tab1_widget = QWidget()
        tab1_layout = QVBoxLayout(tab1_widget)
        self.plot_coeff = pg.PlotWidget(title="空力係数 (CL / CD)")
        self.plot_coeff.showGrid(x=True, y=True)
        self.plot_coeff.setLabel('left', 'Coefficient')
        self.plot_coeff.addLegend()
        self.curve_cl_raw = self.plot_coeff.plot(pen=pg.mkPen('#ffccd5', width=1), name="CL (Raw)")
        self.curve_cl_filt = self.plot_coeff.plot(pen=pg.mkPen('#e63946', width=2), name="CL (Filtered)")
        self.curve_cd_raw = self.plot_coeff.plot(pen=pg.mkPen('#d8f3dc', width=1), name="CD (Raw)")
        self.curve_cd_filt = self.plot_coeff.plot(pen=pg.mkPen('#2d6a4f', width=2), name="CD (Filtered)")

        self.region = pg.LinearRegionItem()
        self.region.setZValue(10)
        self.plot_coeff.addItem(self.region)
        self.region.sigRegionChangeFinished.connect(self.update_analysis_plots_ts)

        self.plot_wind = pg.PlotWidget(title="測定風速 (AvgWind)")
        self.plot_wind.showGrid(x=True, y=True)
        self.plot_wind.setLabel('bottom', 'Time [s]')
        self.plot_wind.setLabel('left', 'Wind Speed [m/s]')
        self.curve_wind = self.plot_wind.plot(pen=pg.mkPen('#f1c40f', width=1.5))
        self.plot_wind.setXLink(self.plot_coeff)
        
        tab1_layout.addWidget(self.plot_coeff, stretch=2)
        tab1_layout.addWidget(self.plot_wind, stretch=1)
        self.ts_tabs.addTab(tab1_widget, "空力性能 (CL/CD)")

        # タブ2-2: 6分力モニター
        tab2_widget = QWidget()
        tab2_layout = QVBoxLayout(tab2_widget)
        self.plot_forces = pg.PlotWidget(title="天秤測定3軸力 (Fx:風下, Fy:右, Fz:下)")
        self.plot_forces.showGrid(x=True, y=True)
        self.plot_forces.setLabel('left', 'Force [N]')
        self.plot_forces.addLegend()
        self.curve_fx = self.plot_forces.plot(pen=pg.mkPen('#e74c3c', width=1.5), name="Fx (風下方向)")
        self.curve_fy = self.plot_forces.plot(pen=pg.mkPen('#3498db', width=1.5), name="Fy (右方向)")
        self.curve_fz = self.plot_forces.plot(pen=pg.mkPen('#95a5a6', width=1), name="Fz (下方向)")

        self.plot_moments = pg.PlotWidget(title="天秤測定モーメント (Mx, My, Mz)")
        self.plot_moments.showGrid(x=True, y=True)
        self.plot_moments.setLabel('bottom', 'Time [s]')
        self.plot_moments.setLabel('left', 'Moment [Nm]')
        self.plot_moments.addLegend()
        self.curve_mx = self.plot_moments.plot(pen=pg.mkPen('#1abc9c', width=1.5), name="Mx (ロール)")
        self.curve_my = self.plot_moments.plot(pen=pg.mkPen('#d35400', width=1.5), name="My (ピッチ)")
        self.curve_mz = self.plot_moments.plot(pen=pg.mkPen('#9b59b6', width=1.5), name="Mz (ヨー)")

        self.plot_forces.setXLink(self.plot_coeff)
        self.plot_moments.setXLink(self.plot_coeff)
        tab2_layout.addWidget(self.plot_forces, stretch=1)
        tab2_layout.addWidget(self.plot_moments, stretch=1)
        self.ts_tabs.addTab(tab2_widget, "6分力生データ")

        # タブ2-3: 風圧中心
        tab3_widget = QWidget()
        tab3_layout = QHBoxLayout(tab3_widget)
        vbox_cop_time = QVBoxLayout()
        self.plot_cop_z = pg.PlotWidget(title="風圧中心高さ Z_CoP (-Mx / Fy)")
        self.plot_cop_z.showGrid(x=True, y=True)
        self.plot_cop_z.setLabel('left', 'Height [m]')
        self.curve_cop_z = self.plot_cop_z.plot(pen=pg.mkPen('#34495e', width=2))

        self.plot_cop_x = pg.PlotWidget(title="風圧中心前後 X_CoP (Mz / Fy)")
        self.plot_cop_x.showGrid(x=True, y=True)
        self.plot_cop_x.setLabel('bottom', 'Time [s]')
        self.plot_cop_x.setLabel('left', 'Position [m]')
        self.curve_cop_x = self.plot_cop_x.plot(pen=pg.mkPen('#16a085', width=2))

        self.plot_cop_z.setXLink(self.plot_coeff)
        self.plot_cop_x.setXLink(self.plot_coeff)
        vbox_cop_time.addWidget(self.plot_cop_z)
        vbox_cop_time.addWidget(self.plot_cop_x)
        tab3_layout.addLayout(vbox_cop_time, stretch=2)

        self.plot_cop_2d = pg.PlotWidget(title="セイル風圧中心 (CoP) 2D断面マッピング")
        self.plot_cop_2d.showGrid(x=True, y=True)
        self.plot_cop_2d.setLabel('bottom', '前後方向 X_CoP [m] (マスト後方)')
        self.plot_cop_2d.setLabel('left', '高さ方向 Z_CoP [m] (マスト上方)')
        self.scatter_cop = pg.ScatterPlotItem(size=6, pen=pg.mkPen(None), brush=pg.mkBrush(22, 160, 133, 100))
        self.plot_cop_2d.addItem(self.scatter_cop)
        self.scatter_cop_avg = pg.ScatterPlotItem(size=14, pen=pg.mkPen('w', width=1.5), brush=pg.mkBrush('#c0392b'))
        self.plot_cop_2d.addItem(self.scatter_cop_avg)
        tab3_layout.addWidget(self.plot_cop_2d, stretch=1)
        self.ts_tabs.addTab(tab3_widget, "風圧中心 (CoP)")

        # タブ2-4: 周波数・分布解析
        tab4_widget = QWidget()
        tab4_layout = QHBoxLayout(tab4_widget)
        vbox_fft = QVBoxLayout()
        self.plot_fft = pg.PlotWidget(title="周波数スペクトル (FFT振幅)")
        self.plot_fft.showGrid(x=True, y=True)
        self.plot_fft.setLabel('bottom', 'Frequency [Hz]')
        self.plot_fft.setLabel('left', 'Amplitude')
        self.curve_fft = self.plot_fft.plot(pen=pg.mkPen('#e67e22', width=2))
        self.v_line_fft = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen('#c0392b', width=1.5, style=Qt.DashLine))
        self.plot_fft.addItem(self.v_line_fft)
        
        self.lbl_peak_freq = QLabel("ピーク周波数: - Hz")
        self.lbl_peak_freq.setStyleSheet("font-weight: bold; color: #d35400; font-size: 12px;")
        vbox_fft.addWidget(self.plot_fft)
        vbox_fft.addWidget(self.lbl_peak_freq)

        self.plot_hist = pg.PlotWidget(title="信号確率分布 (Histogram)")
        self.plot_hist.showGrid(x=True, y=True)
        self.plot_hist.setLabel('bottom', '値')
        self.plot_hist.setLabel('left', 'カウント数')
        self.hist_item = pg.BarGraphItem(x=[], height=[], width=0, brush=pg.mkBrush('#3498db'), pen='k')
        self.plot_hist.addItem(self.hist_item)
        tab4_layout.addLayout(vbox_fft, stretch=1)
        tab4_layout.addWidget(self.plot_hist, stretch=1)
        self.ts_tabs.addTab(tab4_widget, "周波数・分布解析")

        splitter.addWidget(right_panel)

    # =========================================================================
    # 共通ヘルパー / スタイル設定
    # =========================================================================
    def _read_csv_safe(self, filepath):
        try:
            return pd.read_csv(filepath, comment='#', encoding='utf-8')
        except UnicodeDecodeError:
            return pd.read_csv(filepath, comment='#', encoding='cp932')

    def _clean_dataframe_columns(self, df):
        df.columns = df.columns.str.strip().str.strip("'").str.strip('"')
        return df

    def parse_metadata_header(self, filepath):
        metadata = {"Memo": "なし", "Area": 0.1875}
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.startswith('#'):
                        content = line.replace('#', '').strip()
                        if "Memo:" in content:
                            match = re.search(r'Memo:\s*,\s*"?([^"\n]+)"?', content)
                            if match:
                                metadata["Memo"] = match.group(1)
                        elif "Sail Area" in content:
                            match = re.search(r'Sail Area[^:]*:\s*,\s*([0-9\.]+)', content)
                            if match:
                                metadata["Area"] = float(match.group(1))
                    else:
                        break
        except Exception as e:
            print(f"Error parsing metadata: {e}")
        return metadata

    def apply_stylesheet(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #f8f9fa; }
            QGroupBox {
                font-size: 13px; font-weight: bold; border: 2px solid #bdc3c7;
                border-radius: 6px; margin-top: 8px; padding-top: 12px;
                background-color: #ffffff; color: #2c3e50;
            }
            QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 10px; padding: 0 4px; }
            QLabel { color: #2c3e50; }
            QPushButton {
                background-color: #2c3e50; color: white; font-weight: bold;
                border: none; border-radius: 4px; padding: 6px;
            }
            QPushButton:hover { background-color: #34495e; }
            QLineEdit, QComboBox, QSpinBox {
                border: 1px solid #bdc3c7; border-radius: 4px; padding: 3px;
                background-color: #ffffff; color: #2c3e50;
            }
            QComboBox QAbstractItemView {
                background-color: #ffffff; color: #2c3e50;
                selection-background-color: #34495e; selection-color: #ffffff; border: 1px solid #bdc3c7;
            }
            QTableWidget { border: 1px solid #bdc3c7; border-radius: 4px; background-color: #ffffff; color: #2c3e50; }
            QHeaderView::section { background-color: #f2f4f4; font-weight: bold; color: #2c3e50; }
            QTabWidget::pane { border: 1px solid #bdc3c7; background: white; border-radius: 4px; }
            QTabBar::tab {
                background: #e5e8e8; border: 1px solid #bdc3c7; padding: 6px 12px;
                margin-right: 2px; border-top-left-radius: 4px; border-top-right-radius: 4px;
                font-weight: bold; color: #5d6d7e;
            }
            QTabBar::tab:selected { background: white; border-bottom-color: transparent; color: #2c3e50; }
        """)

    # =========================================================================
    # タブ1: データインポート & 要約解析ロジック
    # =========================================================================
    def _get_mean_values(self, filepath):
        df = self._read_csv_safe(filepath)
        df = self._clean_dataframe_columns(df)
        forces = ['Fx(N)', 'Fy(N)', 'Fz(N)', 'Temperature', 'Humidity', 'Pressure', 'AvgWind']
        for col in forces:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df = df.dropna(subset=forces)
        if len(df) == 0:
            raise ValueError("No valid numeric data found in CSV.")
        return df[forces].mean()

    def process_csv_files(self, csv_files, dataset_name, temp_dir=None):
        """解析処理。一時ディレクトリ情報 (temp_dir) も記録する"""
        try:
            area = float(self.input_area_overall.text())
        except ValueError:
            QMessageBox.warning(self, "Warning", "Invalid Sail Area value.")
            return False

        pair_map = {}
        for filepath in csv_files:
            filename = os.path.basename(filepath)
            name_we, _ = os.path.splitext(filename)
            parts = name_we.split('_')
            if len(parts) < 3: continue 
            try:
                angle_str = parts[-1]
                scene_str = parts[-2].lower()
                angle = float(angle_str)
                scene = 'tare' if 'tare' in scene_str else 'test' if 'test' in scene_str else None
                if scene is None: continue
                if angle not in pair_map:
                    pair_map[angle] = {'tare': None, 'test': None}
                pair_map[angle][scene] = filepath
            except ValueError:
                continue

        new_rows = []
        for angle, pairs in pair_map.items():
            tare_path, test_path = pairs['tare'], pairs['test']
            if not tare_path or not test_path: continue
            try:
                tare_means = self._get_mean_values(tare_path)
                test_means = self._get_mean_values(test_path)
                net_fx = test_means['Fx(N)'] - tare_means['Fx(N)']
                net_fy = test_means['Fy(N)'] - tare_means['Fy(N)']

                Tk = test_means['Temperature'] + 273.15
                Es = 6.1078 * 10.0 ** ((7.5 * test_means['Temperature']) / (test_means['Temperature'] + 237.3))
                Pv = Es * (test_means['Humidity'] / 100.0)
                Pd = test_means['Pressure'] - Pv
                rho = (Pd * 100.0 / (287.05 * Tk)) + (Pv * 100.0 / (461.495 * Tk))
                q = 0.5 * rho * (test_means['AvgWind'] ** 2)
                if q < 0.5: continue

                cl = net_fy / (q * area)
                cd = net_fx / (q * area)
                l_d = cl / cd if cd != 0 else 0

                new_rows.append({'Angle': angle, 'CL': cl, 'CD': cd, 'L_D': l_d})
            except Exception as e:
                print(f"Failed to process angle {angle}: {e}")
                continue

        if not new_rows: return False

        df = pd.DataFrame(new_rows).astype(float).sort_values(by='Angle').reset_index(drop=True)
        color = COLOR_PALETTE[self.color_index % len(COLOR_PALETTE)]
        self.color_index += 1

        original_name = dataset_name
        counter = 1
        while dataset_name in self.datasets:
            dataset_name = f"{original_name}_{counter}"
            counter += 1

        # データセットに一時フォルダ情報を追加
        self.datasets[dataset_name] = {
            'df': df, 
            'color': color, 
            'visible': True, 
            'pair_map': pair_map,
            'temp_dir': temp_dir
        }
        self.add_dataset_to_list_widget(dataset_name, color)
        
        self.update_ts_dataset_combo()
        return True

    def select_and_analyze_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select Data Directory", "")
        if not dir_path: return
        csv_files = glob.glob(os.path.join(dir_path, "*.csv"))
        if not csv_files:
            QMessageBox.warning(self, "No Files", "No CSV files found.")
            return
        dataset_name = os.path.basename(os.path.normpath(dir_path))
        if self.process_csv_files(csv_files, dataset_name, temp_dir=None):
            self.update_plots()
            self.select_dataset_by_name(dataset_name)

    def select_and_analyze_archive(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Archive File", "", "Archive Files (*.7z *.zip)")
        if not file_path: return
        _, ext = os.path.splitext(file_path.lower())
        if ext == '.7z' and not HAS_PY7ZR:
            QMessageBox.critical(self, "Missing py7zr", "pip install py7zr required.")
            return

        # 一時ディレクトリの作成
        temp_dir = tempfile.mkdtemp()
        try:
            if ext == '.7z':
                with py7zr.SevenZipFile(file_path, mode='r') as archive: 
                    archive.extractall(path=temp_dir)
            elif ext == '.zip':
                with zipfile.ZipFile(file_path, 'r') as archive: 
                    archive.extractall(path=temp_dir)

            csv_files = []
            for root, _, filenames in os.walk(temp_dir):
                for f in filenames:
                    if f.lower().endswith('.csv'): 
                        csv_files.append(os.path.join(root, f))

            if not csv_files:
                QMessageBox.warning(self, "No Files", "No CSV files found inside archive.")
                shutil.rmtree(temp_dir, ignore_errors=True)
                return

            dataset_name = os.path.basename(file_path)
            # 解析成功時は temp_dir を消さずに、登録データとして生存させる
            success = self.process_csv_files(csv_files, dataset_name, temp_dir=temp_dir)
            if success:
                self.update_plots()
                self.select_dataset_by_name(dataset_name)
            else:
                shutil.rmtree(temp_dir, ignore_errors=True)
                QMessageBox.warning(self, "No Valid Data", "No valid Tare/Test pairs found.")

        except Exception as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
            QMessageBox.critical(self, "Error", f"Failed to extract archive:\n{e}")

    def add_dataset_to_list_widget(self, name, color_hex):
        item = QListWidgetItem(name)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
        item.setCheckState(Qt.Checked)
        item.setForeground(QColor(color_hex))
        self.dataset_list.addItem(item)

    def select_dataset_by_name(self, name):
        for i in range(self.dataset_list.count()):
            if self.dataset_list.item(i).text() == name:
                self.dataset_list.setCurrentItem(self.dataset_list.item(i))
                break

    def on_dataset_visibility_changed(self, item):
        if item.text() in self.datasets:
            self.datasets[item.text()]['visible'] = (item.checkState() == Qt.Checked)
            self.update_plots()

    def on_dataset_selection_changed(self):
        selected = self.dataset_list.selectedItems()
        if not selected:
            self.table.setRowCount(0)
            return
        name = selected[0].text()
        if name in self.datasets:
            df = self.datasets[name]['df']
            self.table.setRowCount(len(df))
            for row, idx in enumerate(df.index):
                self.table.setItem(row, 0, QTableWidgetItem(f"{df.loc[idx, 'Angle']:.1f}"))
                self.table.setItem(row, 1, QTableWidgetItem(f"{df.loc[idx, 'CL']:.4f}"))
                self.table.setItem(row, 2, QTableWidgetItem(f"{df.loc[idx, 'CD']:.4f}"))
                self.table.setItem(row, 3, QTableWidgetItem(f"{df.loc[idx, 'L_D']:.2f}"))

    def update_plots(self):
        self.plot_cl.clear()
        self.plot_cd.clear()
        self.plot_polar.clear()
        for name, data in self.datasets.items():
            if not data['visible']: continue
            df, color = data['df'], data['color']
            angles = np.array(df['Angle'].values, dtype=float)
            cls, cds = np.array(df['CL'].values, dtype=float), np.array(df['CD'].values, dtype=float)
            pen = pg.mkPen(color, width=2)
            self.plot_cl.plot(x=angles, y=cls, name=name, pen=pen, symbol='o', symbolBrush=color, symbolSize=6)
            self.plot_cd.plot(x=angles, y=cds, name=name, pen=pen, symbol='o', symbolBrush=color, symbolSize=6)
            self.plot_polar.plot(x=cds, y=cls, name=name, pen=pen, symbol='s', symbolBrush=color, symbolSize=6)

    def delete_selected_dataset(self):
        selected = self.dataset_list.selectedItems()
        if not selected: return
        name = selected[0].text()
        if QMessageBox.question(self, "Confirm", f"Delete '{name}'?", QMessageBox.Yes|QMessageBox.No) == QMessageBox.Yes:
            if name in self.datasets:
                # 一時ディレクトリがあれば削除
                temp_dir = self.datasets[name].get('temp_dir')
                if temp_dir:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                del self.datasets[name]
            for i in range(self.dataset_list.count()):
                if self.dataset_list.item(i).text() == name:
                    self.dataset_list.takeItem(i)
                    break
            self.update_plots()
            self.on_dataset_selection_changed()
            self.update_ts_dataset_combo()

    def clear_all_datasets(self):
        if not self.datasets: return
        if QMessageBox.question(self, "Clear", "Delete all?", QMessageBox.Yes|QMessageBox.No) == QMessageBox.Yes:
            # すべての一時フォルダを削除
            for name, data in self.datasets.items():
                temp_dir = data.get('temp_dir')
                if temp_dir:
                    shutil.rmtree(temp_dir, ignore_errors=True)
            self.datasets.clear()
            self.dataset_list.clear()
            self.table.setRowCount(0)
            self.update_plots()
            self.update_ts_dataset_combo()

    def export_csv(self):
        selected = self.dataset_list.selectedItems()
        if not selected or selected[0].text() not in self.datasets: return
        name = selected[0].text()
        filepath, _ = QFileDialog.getSaveFileName(self, "Save CSV", f"wind_tunnel_{name}_summary.csv", "CSV Files (*.csv)")
        if filepath: self.datasets[name]['df'].to_csv(filepath, index=False)

    # =========================================================================
    # タブ2: 時系列解析連動・選択ロジック
    # =========================================================================
    def update_ts_dataset_combo(self):
        """全体表示タブ側のデータセット一覧を時系列側のプルダウンに同期"""
        self.combo_ts_dataset.blockSignals(True)
        self.combo_ts_dataset.clear()
        self.combo_ts_dataset.addItems(list(self.datasets.keys()))
        self.combo_ts_dataset.blockSignals(False)
        self.on_ts_dataset_changed()

    def on_ts_dataset_changed(self):
        """選択データセットが切り替わったら、有効な角度プルダウンを再構築"""
        ds_name = self.combo_ts_dataset.currentText()
        self.combo_ts_angle.blockSignals(True)
        self.combo_ts_angle.clear()
        if ds_name in self.datasets:
            angles = sorted(list(self.datasets[ds_name]['pair_map'].keys()))
            self.combo_ts_angle.addItems([f"{a:.1f}" for a in angles])
        self.combo_ts_angle.blockSignals(False)
        self.on_ts_angle_changed()

    def on_ts_angle_changed(self):
        """プルダウンから指定された該当角度の時系列生ファイルを特定して直接内部読込"""
        ds_name = self.combo_ts_dataset.currentText()
        angle_str = self.combo_ts_angle.currentText()
        if not ds_name or not angle_str:
            self.ts_df = None
            return

        angle = float(angle_str)
        pairs = self.datasets[ds_name]['pair_map'][angle]
        
        test_path = pairs['test']
        tare_path = pairs['tare']
        
        self.load_time_series_raw(test_path, tare_path)

    def load_time_series_raw(self, test_path, tare_path):
        """特定されたファイルパスから時系列生データのロード及び前処理を行う"""
        if not test_path: return
        try:
            # 1. 試験データの読み込み
            meta = self.parse_metadata_header(test_path)
            self.lbl_meta_memo.setText(f"メモ: {meta['Memo']}")
            self.lbl_meta_area.setText(f"代表面積 (Header): {meta['Area']} m²")
            self.input_area_ts.setText(str(meta['Area']))
            
            df = self._read_csv_safe(test_path)
            df = self._clean_dataframe_columns(df)
            
            req_cols = ['Timestamp', 'Fx(N)', 'Fy(N)', 'Fz(N)', 'Mx(Nm)', 'My(Nm)', 'Mz(Nm)', 'AvgWind']
            missing = [c for c in req_cols if c not in df.columns]
            if missing: raise ValueError(f"必要列不足: {missing}")

            df['Timestamp'] = pd.to_datetime(df['Timestamp'], errors='coerce')
            df = df.dropna(subset=['Timestamp']).sort_values('Timestamp').reset_index(drop=True)
            start_time = df['Timestamp'].iloc[0]
            df['Time_s'] = (df['Timestamp'] - start_time).dt.total_seconds()

            total_duration = df['Time_s'].iloc[-1]
            dt = np.mean(np.diff(df['Time_s'].values)) if len(df) > 1 else 1.0
            fs = 1.0 / dt if dt > 0 else 1.0

            self.lbl_meta_duration.setText(f"測定時間: {total_duration:.2f} 秒 ({len(df)} 点)")
            self.lbl_meta_rate.setText(f"周波数: {fs:.2f} Hz (dt={dt*1000:.1f}ms)")
            self.lbl_test_file.setText(os.path.basename(test_path))

            self.ts_df = df

            # 2. Tareデータの平均オフセット算出
            if tare_path:
                tare_df = self._read_csv_safe(tare_path)
                tare_df = self._clean_dataframe_columns(tare_df)
                cols = ['Fx(N)', 'Fy(N)', 'Fz(N)', 'Mx(Nm)', 'My(Nm)', 'Mz(Nm)']
                self.tare_fx_avg = pd.to_numeric(tare_df['Fx(N)'], errors='coerce').mean()
                self.tare_fy_avg = pd.to_numeric(tare_df['Fy(N)'], errors='coerce').mean()
                self.tare_fz_avg = pd.to_numeric(tare_df['Fz(N)'], errors='coerce').mean()
                self.tare_mx_avg = pd.to_numeric(tare_df['Mx(Nm)'], errors='coerce').mean()
                self.tare_my_avg = pd.to_numeric(tare_df['My(Nm)'], errors='coerce').mean()
                self.tare_mz_avg = pd.to_numeric(tare_df['Mz(Nm)'], errors='coerce').mean()
                self.lbl_tare_file.setText(os.path.basename(tare_path))
            else:
                self.tare_fx_avg = self.tare_fy_avg = self.tare_fz_avg = 0.0
                self.tare_mx_avg = self.tare_my_avg = self.tare_mz_avg = 0.0
                self.lbl_tare_file.setText("Tare未適用 (0点オフセット想定)")

            self.recalculate_ts_and_plot()
            self.region.setRegion([total_duration * 0.1, total_duration * 0.9])
        except Exception as e:
            QMessageBox.critical(self, "Error", f"時系列解析データの展開に失敗:\n{e}")

    # =========================================================================
    # タブ2: 計算・信号処理ロジック
    # =========================================================================
    def recalculate_ts_if_needed(self):
        if self.ts_df is not None: self.recalculate_ts_and_plot()

    def recalculate_ts_and_plot(self):
        if self.ts_df is None: return
        try:
            area = float(self.input_area_ts.text()) if self.input_area_ts.text() else 0.1875
            df = self.ts_df
            
            temp, hum, press, wind = [pd.to_numeric(df[c], errors='coerce') for c in ['Temperature', 'Humidity', 'Pressure', 'AvgWind']]
            fx, fy, fz = [pd.to_numeric(df[c], errors='coerce') for c in ['Fx(N)', 'Fy(N)', 'Fz(N)']]
            mx, my, mz = [pd.to_numeric(df[c], errors='coerce') for c in ['Mx(Nm)', 'My(Nm)', 'Mz(Nm)']]

            if self.cb_recalc.isChecked():
                df['Fx_net'], df['Fy_net'], df['Fz_net'] = fx - self.tare_fx_avg, fy - self.tare_fy_avg, fz - self.tare_fz_avg
                df['Mx_net'], df['My_net'], df['Mz_net'] = mx - self.tare_mx_avg, my - self.tare_my_avg, mz - self.tare_mz_avg
            else:
                df['Fx_net'], df['Fy_net'], df['Fz_net'] = fx, fy, fz
                df['Mx_net'], df['My_net'], df['Mz_net'] = mx, my, mz

            Tk = temp + 273.15
            Es = 6.1078 * 10.0 ** ((7.5 * temp) / (temp + 237.3))
            Pv = Es * (hum / 100.0)
            rho = ((press - Pv) * 100.0 / (287.05 * Tk)) + (Pv * 100.0 / (461.495 * Tk))
            q = 0.5 * rho * (wind ** 2)

            df['CL_active'] = np.where(q > 0.5, df['Fy_net'] / (q * area), 0.0)
            df['CD_active'] = np.where(q > 0.5, df['Fx_net'] / (q * area), 0.0)

            safe_fy = np.where(np.abs(df['Fy_net']) > 0.01, df['Fy_net'], np.nan)
            df['CoP_Z'] = -df['Mx_net'] / safe_fy
            df['CoP_X'] = df['Mz_net'] / safe_fy
            df['CoP_Z'] = df['CoP_Z'].ffill().bfill().fillna(0.0)
            df['CoP_X'] = df['CoP_X'].ffill().bfill().fillna(0.0)

            self.apply_filter_and_update_ts()
        except Exception as e:
            print(f"Error in recalculate_ts_and_plot: {e}")

    def apply_filter_and_update_ts(self):
        if self.ts_df is None: return
        method, window = self.combo_filter.currentText(), self.spin_window.value()
        df = self.ts_df

        if "移動平均" in method:
            df['CL_filt'] = df['CL_active'].rolling(window=window, min_periods=1, center=True).mean()
            df['CD_filt'] = df['CD_active'].rolling(window=window, min_periods=1, center=True).mean()
        elif "指数移動平均" in method:
            df['CL_filt'] = df['CL_active'].ewm(span=window, adjust=False).mean()
            df['CD_filt'] = df['CD_active'].ewm(span=window, adjust=False).mean()
        else:
            df['CL_filt'], df['CD_filt'] = df['CL_active'], df['CD_active']

        t = df['Time_s'].values
        self.curve_cl_raw.setData(t, df['CL_active'].values)
        self.curve_cl_filt.setData(t, df['CL_filt'].values)
        self.curve_cd_raw.setData(t, df['CD_active'].values)
        self.curve_cd_filt.setData(t, df['CD_filt'].values)
        self.curve_wind.setData(t, pd.to_numeric(df['AvgWind']).values)

        self.curve_fx.setData(t, df['Fx_net'].values)
        self.curve_fy.setData(t, df['Fy_net'].values)
        self.curve_fz.setData(t, df['Fz_net'].values)
        self.curve_mx.setData(t, df['Mx_net'].values)
        self.curve_my.setData(t, df['My_net'].values)
        self.curve_mz.setData(t, df['Mz_net'].values)

        self.curve_cop_z.setData(t, df['CoP_Z'].values)
        self.curve_cop_x.setData(t, df['CoP_X'].values)

        self.update_analysis_plots_ts()

    def update_analysis_plots_ts(self):
        if self.ts_df is None: return
        t_min, t_max = self.region.getRegion()
        sub_df = self.ts_df[(self.ts_df['Time_s'] >= t_min) & (self.ts_df['Time_s'] <= t_max)]
        num_pts = len(sub_df)
        self.lbl_region_info.setText(f"選択範囲: {t_min:.2f}秒 〜 {t_max:.2f}秒 ({num_pts}点)")
        if num_pts < 4: return

        # 統計テーブルの更新
        vars_to_show = [
            ("CL (Filtered)", sub_df['CL_filt']), ("CD (Filtered)", sub_df['CD_filt']),
            ("Fx_net (N)", sub_df['Fx_net']), ("Fy_net (N)", sub_df['Fy_net']),
            ("Mz_net (Nm)", sub_df['Mz_net']), ("CoP_Z (Height m)", sub_df['CoP_Z'])
        ]
        for row, (name, series) in enumerate(vars_to_show):
            valid = series.dropna()
            if len(valid) == 0: continue
            self.table_stats.setItem(row, 0, QTableWidgetItem(name))
            self.table_stats.setItem(row, 1, QTableWidgetItem(f"{valid.mean():.4f}"))
            self.table_stats.setItem(row, 2, QTableWidgetItem(f"{valid.std():.4f}"))
            self.table_stats.setItem(row, 3, QTableWidgetItem(f"{np.sqrt(np.mean(valid**2)):.4f}"))
            self.table_stats.setItem(row, 4, QTableWidgetItem(f"{valid.max():.3f} / {valid.min():.3f}"))

        # CoP 2D 散布図マッピング
        x_cop, z_cop = sub_df['CoP_X'].values, sub_df['CoP_Z'].values
        if len(x_cop) > 1000:
            step = len(x_cop) // 1000
            self.scatter_cop.setData(x=x_cop[::step], y=z_cop[::step])
        else:
            self.scatter_cop.setData(x=x_cop, y=z_cop)
        self.scatter_cop_avg.setData(x=[np.mean(x_cop)], y=[np.mean(z_cop)])

        # FFT & ヒストグラム
        target_var = self.combo_var.currentText()
        var_map = {"CL": 'CL_filt', "CD": 'CD_filt', "Fx(N)": 'Fx_net', "Fy(N)": 'Fy_net', "Mz(Nm)": 'Mz_net', "CoP_Z(m)": 'CoP_Z'}
        y_data = sub_df[var_map[target_var]].values
        dt = np.mean(np.diff(sub_df['Time_s'].values)) if len(sub_df) > 1 else 0.1

        if dt > 0:
            y_detrend = y_data - np.mean(y_data)
            n = len(y_detrend)
            xf = np.fft.rfftfreq(n, d=dt)
            amp = (2.0 / n) * np.abs(np.fft.rfft(y_detrend))
            self.curve_fft.setData(xf, amp)
            self.plot_fft.setTitle(f"【FFT】{target_var} の周波数振幅")
            if len(amp) > 2:
                p_idx = np.argmax(amp[1:]) + 1
                self.v_line_fft.setValue(xf[p_idx])
                self.lbl_peak_freq.setText(f"ピーク周波数: {xf[p_idx]:.3f} Hz (振幅: {amp[p_idx]:.5f})")
        
        counts, bins = np.histogram(y_data, bins='auto')
        self.hist_item.setOpts(x=0.5*(bins[:-1]+bins[1:]), height=counts, width=np.diff(bins))
        self.plot_hist.setTitle(f"【分布】{target_var} のヒストグラム")

    def export_region_data(self):
        if self.ts_df is None: return
        t_min, t_max = self.region.getRegion()
        sub_df = self.ts_df[(self.ts_df['Time_s'] >= t_min) & (self.ts_df['Time_s'] <= t_max)]
        filepath, _ = QFileDialog.getSaveFileName(self, "Export Range Data", "selected_range_extract.csv", "CSV Files (*.csv)")
        if filepath: sub_df.to_csv(filepath, index=False)

    def export_stats_summary(self):
        if self.ts_df is None: return
        filepath, _ = QFileDialog.getSaveFileName(self, "Save Report", "stats_summary_report.txt", "Text Files (*.txt)")
        if filepath:
            try:
                t_min, t_max = self.region.getRegion()
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(f"=== 風洞試験 選択窓内解析・統計サマリーレポート ===\n\n")
                    f.write(f"解析時間幅: {t_min:.2f} s 〜 {t_max:.2f} s\n")
                    f.write(f"Tareオフセット補正: {'有効' if self.cb_recalc.isChecked() else '無効'}\n\n")
                    for row in range(self.table_stats.rowCount()):
                        v = [self.table_stats.item(row, col).text() if self.table_stats.item(row, col) else "" for col in range(5)]
                        f.write(f"{v[0]:<25} | 平均:{v[1]:<10} | SD:{v[2]:<10} | RMS:{v[3]:<10} | Max/Min:{v[4]}\n")
                QMessageBox.information(self, "Success", "Report saved successfully.")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save:\n{e}")

    def closeEvent(self, event):
        """アプリケーション終了時に残留している一時フォルダをすべて削除する"""
        for name, data in self.datasets.items():
            temp_dir = data.get('temp_dir')
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = IntegratedWindTunnelAnalyzer()
    window.show()
    sys.exit(app.exec())
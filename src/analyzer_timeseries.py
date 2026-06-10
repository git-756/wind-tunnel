import sys
import re
import pandas as pd
import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QPushButton, QLabel, QFileDialog, 
                               QMessageBox, QFormLayout, QLineEdit, QGroupBox,
                               QComboBox, QSpinBox, QTableWidget, QTableWidgetItem, 
                               QHeaderView, QSplitter, QCheckBox, QTabWidget)
from PySide6.QtCore import Qt

class TimeSeriesAnalyzer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Wind Tunnel Time-Series Analyzer (時系列解析モード)")
        self.resize(1440, 950)

        # データ管理用変数
        self.df = None
        self.tare_fx_avg = 0.0
        self.tare_fy_avg = 0.0
        self.tare_fz_avg = 0.0
        self.tare_mx_avg = 0.0
        self.tare_my_avg = 0.0
        self.tare_mz_avg = 0.0
        
        self.setup_ui()
        self.apply_stylesheet()

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        # 左右を分割するスプリッター (左: 設定/統計, 右: グラフタブ)
        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)

        # ==================== 左パネル: コントロール & 統計 ====================
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)
        left_panel.setMinimumWidth(380)
        left_panel.setMaximumWidth(450)

        # 1. ファイル読込 & メタデータ表示
        group_io = QGroupBox("1. データファイルの読み込み")
        vbox_io = QVBoxLayout()
        
        self.btn_load_test = QPushButton("試験時系列 CSV を選択")
        self.btn_load_test.clicked.connect(self.load_test_csv)
        self.lbl_test_file = QLabel("試験データ未ロード")
        self.lbl_test_file.setWordWrap(True)
        self.lbl_test_file.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        
        self.btn_load_tare = QPushButton("Tare CSV (風速0) を選択 [任意]")
        self.btn_load_tare.clicked.connect(self.load_tare_csv)
        self.lbl_tare_file = QLabel("Tare未適用 (0点オフセット想定)")
        self.lbl_tare_file.setWordWrap(True)
        self.lbl_tare_file.setStyleSheet("color: #7f8c8d; font-size: 11px;")

        # メタデータ表示部
        self.lbl_meta_memo = QLabel("メモ: -")
        self.lbl_meta_area = QLabel("代表面積: - m²")
        self.lbl_meta_duration = QLabel("測定時間: - 秒")
        self.lbl_meta_rate = QLabel("サンプリング周波数: - Hz")
        for lbl in [self.lbl_meta_memo, self.lbl_meta_area, self.lbl_meta_duration, self.lbl_meta_rate]:
            lbl.setStyleSheet("font-size: 11px; color: #2c3e50; font-weight: bold;")

        vbox_io.addWidget(self.btn_load_test)
        vbox_io.addWidget(self.lbl_test_file)
        vbox_io.addWidget(self.btn_load_tare)
        vbox_io.addWidget(self.lbl_tare_file)
        vbox_io.addWidget(self.lbl_meta_memo)
        vbox_io.addWidget(self.lbl_meta_area)
        vbox_io.addWidget(self.lbl_meta_duration)
        vbox_io.addWidget(self.lbl_meta_rate)
        group_io.setLayout(vbox_io)
        left_layout.addWidget(group_io)

        # 2. 解析設定
        group_settings = QGroupBox("2. 解析・計算設定")
        form_settings = QFormLayout()
        
        self.input_area = QLineEdit("0.25")
        self.input_area.textChanged.connect(self.recalculate_if_needed)
        form_settings.addRow("代表面積 S [m²]:", self.input_area)

        self.cb_recalc = QCheckBox("Tare (ゼロ点引算) 補正を有効化")
        self.cb_recalc.setChecked(False)
        self.cb_recalc.stateChanged.connect(self.recalculate_if_needed)
        form_settings.addRow(self.cb_recalc)

        # 信号処理（フィルタ）設定
        self.combo_filter = QComboBox()
        self.combo_filter.addItems(["なし (生データ)", "移動平均 (Moving Average)", "指数移動平均 (EMA)"])
        self.combo_filter.currentIndexChanged.connect(self.apply_filter_and_update)
        form_settings.addRow("ノイズ低減フィルタ:", self.combo_filter)

        self.spin_window = QSpinBox()
        self.spin_window.setRange(1, 500)
        self.spin_window.setValue(10)
        self.spin_window.setSuffix(" pts")
        self.spin_window.valueChanged.connect(self.apply_filter_and_update)
        form_settings.addRow("フィルタ窓幅:", self.spin_window)

        group_settings.setLayout(form_settings)
        left_layout.addWidget(group_settings)

        # 3. 信号解析の対象指定
        group_analysis_var = QGroupBox("3. 信号・周波数解析の対象")
        form_var = QFormLayout()
        self.combo_var = QComboBox()
        self.combo_var.addItems(["CL", "CD", "Fx(N)", "Fy(N)", "Mz(Nm)", "CoP_Z(m)"])
        self.combo_var.currentIndexChanged.connect(self.update_analysis_plots)
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

        # エクスポートボタン
        hbox_export = QHBoxLayout()
        self.btn_export_region = QPushButton("選択範囲のデータをCSV保存")
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

        # ==================== 右パネル: 多機能タブ付きグラフ ====================
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # タブコントロールの導入
        self.tabs = QTabWidget()
        right_layout.addWidget(self.tabs)

        # 各プロットの設定
        pg.setConfigOption('background', 'w')
        pg.setConfigOption('foreground', 'k')
        pg.setConfigOption('antialias', True)

        # --- 【タブ1: 空力性能 (CL & CD)】 ---
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

        # 範囲選択バー (sigRegionChangeFinishedにより軽量化)
        self.region = pg.LinearRegionItem()
        self.region.setZValue(10)
        self.plot_coeff.addItem(self.region)
        self.region.sigRegionChangeFinished.connect(self.update_analysis_plots)

        self.plot_wind = pg.PlotWidget(title="測定風速 (AvgWind)")
        self.plot_wind.showGrid(x=True, y=True)
        self.plot_wind.setLabel('bottom', 'Time [s]')
        self.plot_wind.setLabel('left', 'Wind Speed [m/s]')
        self.curve_wind = self.plot_wind.plot(pen=pg.mkPen('#f1c40f', width=1.5))
        
        self.plot_wind.setXLink(self.plot_coeff) # X軸同期
        
        tab1_layout.addWidget(self.plot_coeff, stretch=2)
        tab1_layout.addWidget(self.plot_wind, stretch=1)
        self.tabs.addTab(tab1_widget, "空力性能 (CL/CD)")

        # --- 【タブ2: 6分力モニター】 ---
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
        self.tabs.addTab(tab2_widget, "6分力生データ")

        # --- 【タブ3: 風圧中心 (CoP) 解析】 ---
        tab3_widget = QWidget()
        tab3_layout = QHBoxLayout(tab3_widget)

        # 左半分: 高さ方向と前後方向のCoP時系列プロット
        vbox_cop_time = QVBoxLayout()
        self.plot_cop_z = pg.PlotWidget(title="風圧中心高さ Z_CoP (-Mx / Fy)")
        self.plot_cop_z.showGrid(x=True, y=True)
        self.plot_cop_z.setLabel('left', 'Height [m] (上がマイナス)')
        self.curve_cop_z = self.plot_cop_z.plot(pen=pg.mkPen('#34495e', width=2))

        self.plot_cop_x = pg.PlotWidget(title="風圧中心前後 X_CoP (Mz / Fy)")
        self.plot_cop_x.showGrid(x=True, y=True)
        self.plot_cop_x.setLabel('bottom', 'Time [s]')
        self.plot_cop_x.setLabel('left', 'Position [m] (風下方向)')
        self.curve_cop_x = self.plot_cop_x.plot(pen=pg.mkPen('#16a085', width=2))

        self.plot_cop_z.setXLink(self.plot_coeff)
        self.plot_cop_x.setXLink(self.plot_coeff)
        
        vbox_cop_time.addWidget(self.plot_cop_z)
        vbox_cop_time.addWidget(self.plot_cop_x)
        tab3_layout.addLayout(vbox_cop_time, stretch=2)

        # 右半分: CoPの2D断面位置マッピング
        self.plot_cop_2d = pg.PlotWidget(title="セイル風圧中心 (CoP) 2D断面マッピング")
        self.plot_cop_2d.showGrid(x=True, y=True)
        self.plot_cop_2d.setLabel('bottom', '前後方向 X_CoP [m] (マスト後方)')
        self.plot_cop_2d.setLabel('left', '高さ方向 Z_CoP [m] (マスト上方)')
        self.scatter_cop = pg.ScatterPlotItem(size=6, pen=pg.mkPen(None), brush=pg.mkBrush(22, 160, 133, 100))
        self.plot_cop_2d.addItem(self.scatter_cop)
        self.scatter_cop_avg = pg.ScatterPlotItem(size=14, pen=pg.mkPen('w', width=1.5), brush=pg.mkBrush('#c0392b'))
        self.plot_cop_2d.addItem(self.scatter_cop_avg)
        
        tab3_layout.addWidget(self.plot_cop_2d, stretch=1)
        self.tabs.addTab(tab3_widget, "風圧中心 (CoP)")

        # --- 【タブ4: 信号解析 (FFT ＆ ヒストグラム)】 ---
        tab4_widget = QWidget()
        tab4_layout = QHBoxLayout(tab4_widget)

        # FFT周波数解析表示
        vbox_fft = QVBoxLayout()
        self.plot_fft = pg.PlotWidget(title="周波数スペクトル (FFT振幅)")
        self.plot_fft.showGrid(x=True, y=True)
        self.plot_fft.setLabel('bottom', 'Frequency [Hz]')
        self.plot_fft.setLabel('left', 'Amplitude')
        self.curve_fft = self.plot_fft.plot(pen=pg.mkPen('#e67e22', width=2))
        
        self.v_line_fft = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen('#c0392b', width=1.5, style=Qt.DashLine))
        self.plot_fft.addItem(self.v_line_fft)
        
        self.lbl_peak_freq = QLabel("ピーク周波数: - Hz")
        self.lbl_peak_freq.setStyleSheet("font-weight: bold; color: #d35400; font-size: 12px; margin-top: 2px;")
        vbox_fft.addWidget(self.plot_fft)
        vbox_fft.addWidget(self.lbl_peak_freq)

        # 確率密度分布表示
        self.plot_hist = pg.PlotWidget(title="信号確率分布 (Histogram)")
        self.plot_hist.showGrid(x=True, y=True)
        self.plot_hist.setLabel('bottom', '値')
        self.plot_hist.setLabel('left', 'カウント数')
        self.hist_item = pg.BarGraphItem(x=[], height=[], width=0, brush=pg.mkBrush('#3498db'), pen='k')
        self.plot_hist.addItem(self.hist_item)

        tab4_layout.addLayout(vbox_fft, stretch=1)
        tab4_layout.addWidget(self.plot_hist, stretch=1)
        self.tabs.addTab(tab4_widget, "周波数・分布解析")

        splitter.addWidget(right_panel)

    def apply_stylesheet(self):
        # ★不具合対策：システムのダークテーマと競合して入力欄の文字（QLineEdit, QComboBox, QSpinBox）が
        # 白化・同色化してしまう問題を、color指定を強制追加することで完全に解決
        self.setStyleSheet("""
            QMainWindow {
                background-color: #f8f9fa;
            }
            QGroupBox {
                font-size: 13px;
                font-weight: bold;
                border: 2px solid #bdc3c7;
                border-radius: 6px;
                margin-top: 8px;
                padding-top: 12px;
                background-color: #ffffff;
                color: #2c3e50;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 10px;
                padding: 0 4px;
                color: #2c3e50;
            }
            QLabel {
                color: #2c3e50;
            }
            QPushButton {
                background-color: #2c3e50;
                color: white;
                font-weight: bold;
                border: none;
                border-radius: 4px;
                padding: 6px;
            }
            QPushButton:hover {
                background-color: #34495e;
            }
            /* 入力要素全体のベーステキスト色を固定 */
            QLineEdit, QComboBox, QSpinBox {
                border: 1px solid #bdc3c7;
                border-radius: 4px;
                padding: 3px;
                background-color: #ffffff;
                color: #2c3e50;
            }
            /* プルダウンのドロップダウン項目における文字色・背景色も強制解決 */
            QComboBox QAbstractItemView {
                background-color: #ffffff;
                color: #2c3e50;
                selection-background-color: #34495e;
                selection-color: #ffffff;
                border: 1px solid #bdc3c7;
            }
            QTableWidget {
                border: 1px solid #bdc3c7;
                border-radius: 4px;
                gridline-color: #ecf0f1;
                background-color: #ffffff;
                color: #2c3e50;
            }
            QHeaderView::section {
                background-color: #f2f4f4;
                padding: 3px;
                border: 1px solid #d5dbdb;
                font-weight: bold;
                color: #2c3e50;
            }
            QTabWidget::pane {
                border: 1px solid #bdc3c7;
                background: white;
                border-radius: 4px;
            }
            QTabBar::tab {
                background: #e5e8e8;
                border: 1px solid #bdc3c7;
                padding: 6px 12px;
                margin-right: 2px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                font-weight: bold;
                color: #5d6d7e;
            }
            QTabBar::tab:selected {
                background: white;
                border-bottom-color: transparent;
                color: #2c3e50;
            }
        """)

    # ==================== ユーティリティ ====================
    def _read_csv_safe(self, filepath):
        try:
            return pd.read_csv(filepath, comment='#', encoding='utf-8')
        except UnicodeDecodeError:
            return pd.read_csv(filepath, comment='#', encoding='cp932')

    def _clean_dataframe_columns(self, df):
        df.columns = df.columns.str.strip().str.strip("'").str.strip('"')
        return df

    def parse_metadata_header(self, filepath):
        metadata = {"Memo": "なし", "Area": 0.25}
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

    # ==================== ファイルロード & 解析処理 ====================
    def load_test_csv(self):
        filepath, _ = QFileDialog.getOpenFileName(self, "試験時系列データの選択", "", "CSV Files (*.csv)")
        if not filepath:
            return

        try:
            # ヘッダーメタデータ
            meta = self.parse_metadata_header(filepath)
            self.lbl_meta_memo.setText(f"メモ: {meta['Memo']}")
            self.lbl_meta_area.setText(f"代表面積 (Header): {meta['Area']} m²")
            self.input_area.setText(str(meta['Area']))
            
            # データ読込
            df = self._read_csv_safe(filepath)
            df = self._clean_dataframe_columns(df)
            
            # 必要データの有無確認
            req_cols = ['Timestamp', 'Fx(N)', 'Fy(N)', 'Fz(N)', 'Mx(Nm)', 'My(Nm)', 'Mz(Nm)', 'AvgWind']
            missing = [c for c in req_cols if c not in df.columns]
            if missing:
                raise ValueError(f"必要列が不足しています: {missing}")

            # 相対時間の作成
            df['Timestamp'] = pd.to_datetime(df['Timestamp'], errors='coerce')
            df = df.dropna(subset=['Timestamp']).sort_values('Timestamp').reset_index(drop=True)
            start_time = df['Timestamp'].iloc[0]
            df['Time_s'] = (df['Timestamp'] - start_time).dt.total_seconds()

            total_duration = df['Time_s'].iloc[-1]
            dt = np.mean(np.diff(df['Time_s'].values)) if len(df) > 1 else 1.0
            fs = 1.0 / dt if dt > 0 else 1.0

            self.lbl_meta_duration.setText(f"測定時間: {total_duration:.2f} 秒 ({len(df)} 点)")
            self.lbl_meta_rate.setText(f"周波数: {fs:.2f} Hz (dt={dt*1000:.1f}ms)")

            self.df = df
            self.lbl_test_file.setText(filepath.split('/')[-1] if '/' in filepath else filepath.split('\\')[-1])

            self.recalculate_and_plot()
            self.region.setRegion([total_duration * 0.1, total_duration * 0.9])

        except Exception as e:
            QMessageBox.critical(self, "エラー", f"データの読み込みに失敗しました:\n{str(e)}")

    def load_tare_csv(self):
        if self.df is None:
            QMessageBox.warning(self, "警告", "先に試験データを読み込んでください。")
            return

        filepath, _ = QFileDialog.getOpenFileName(self, "Tare CSV の選択", "", "CSV Files (*.csv)")
        if not filepath:
            return

        try:
            tare_df = self._read_csv_safe(filepath)
            tare_df = self._clean_dataframe_columns(tare_df)
            
            cols = ['Fx(N)', 'Fy(N)', 'Fz(N)', 'Mx(Nm)', 'My(Nm)', 'Mz(Nm)']
            for col in cols:
                if col not in tare_df.columns:
                    raise ValueError(f"Tareファイルに {col} が含まれていません。")

            # 平均ゼロ点誤差の適用
            self.tare_fx_avg = pd.to_numeric(tare_df['Fx(N)'], errors='coerce').mean()
            self.tare_fy_avg = pd.to_numeric(tare_df['Fy(N)'], errors='coerce').mean()
            self.tare_fz_avg = pd.to_numeric(tare_df['Fz(N)'], errors='coerce').mean()
            self.tare_mx_avg = pd.to_numeric(tare_df['Mx(Nm)'], errors='coerce').mean()
            self.tare_my_avg = pd.to_numeric(tare_df['My(Nm)'], errors='coerce').mean()
            self.tare_mz_avg = pd.to_numeric(tare_df['Mz(Nm)'], errors='coerce').mean()

            self.lbl_tare_file.setText(f"適用済: {filepath.split('/')[-1]}")
            self.cb_recalc.setChecked(True)

            self.recalculate_and_plot()

        except Exception as e:
            QMessageBox.critical(self, "エラー", f"Tare誤差算出に失敗しました:\n{str(e)}")

    def recalculate_if_needed(self):
        if self.df is not None:
            self.recalculate_and_plot()

    def recalculate_and_plot(self):
        if self.df is None:
            return

        try:
            area = float(self.input_area.text()) if self.input_area.text() else 0.25
            
            # 各列の数値化
            temp = pd.to_numeric(self.df['Temperature'], errors='coerce')
            hum = pd.to_numeric(self.df['Humidity'], errors='coerce')
            press = pd.to_numeric(self.df['Pressure'], errors='coerce')
            wind = pd.to_numeric(self.df['AvgWind'], errors='coerce')
            
            fx_raw = pd.to_numeric(self.df['Fx(N)'], errors='coerce')
            fy_raw = pd.to_numeric(self.df['Fy(N)'], errors='coerce')
            fz_raw = pd.to_numeric(self.df['Fz(N)'], errors='coerce')
            mx_raw = pd.to_numeric(self.df['Mx(Nm)'], errors='coerce')
            my_raw = pd.to_numeric(self.df['My(Nm)'], errors='coerce')
            mz_raw = pd.to_numeric(self.df['Mz(Nm)'], errors='coerce')

            # Tare引算
            if self.cb_recalc.isChecked():
                self.df['Fx_net'] = fx_raw - self.tare_fx_avg
                self.df['Fy_net'] = fy_raw - self.tare_fy_avg
                self.df['Fz_net'] = fz_raw - self.tare_fz_avg
                self.df['Mx_net'] = mx_raw - self.tare_mx_avg
                self.df['My_net'] = my_raw - self.tare_my_avg
                self.df['Mz_net'] = mz_raw - self.tare_mz_avg
            else:
                self.df['Fx_net'] = fx_raw
                self.df['Fy_net'] = fy_raw
                self.df['Fz_net'] = fz_raw
                self.df['Mx_net'] = mx_raw
                self.df['My_net'] = my_raw
                self.df['Mz_net'] = mz_raw

            # 空気密度と動圧計算
            Tk = temp + 273.15
            Es = 6.1078 * 10.0 ** ((7.5 * temp) / (temp + 237.3))
            Pv = Es * (hum / 100.0)
            Pd = press - Pv
            rho = (Pd * 100.0 / (287.05 * Tk)) + (Pv * 100.0 / (461.495 * Tk))
            
            q = 0.5 * rho * (wind ** 2)

            # 空力係数の計算 (風下方向+x, 風上右向き+yの要件に基づく)
            self.df['CL_active'] = np.where(q > 0.5, self.df['Fy_net'] / (q * area), 0.0)
            self.df['CD_active'] = np.where(q > 0.5, self.df['Fx_net'] / (q * area), 0.0)

            # 風圧中心 (CoP) の計算 (分母が極端に0に近い時の発散抑制)
            safe_fy = np.where(np.abs(self.df['Fy_net']) > 0.01, self.df['Fy_net'], np.nan)
            self.df['CoP_Z'] = -self.df['Mx_net'] / safe_fy  # 高さ (上がマイナス)
            self.df['CoP_X'] = self.df['Mz_net'] / safe_fy   # 前後 (風下方向)

            # データの補填処理
            self.df['CoP_Z'] = self.df['CoP_Z'].ffill().bfill().fillna(0.0)
            self.df['CoP_X'] = self.df['CoP_X'].ffill().bfill().fillna(0.0)

            self.apply_filter_and_update()

        except Exception as e:
            QMessageBox.critical(self, "エラー", f"解析処理中に問題が発生しました:\n{str(e)}")

    # ==================== フィルタ＆時系列描画 ====================
    def apply_filter_and_update(self):
        if self.df is None:
            return

        method = self.combo_filter.currentText()
        window = self.spin_window.value()

        if "移動平均" in method:
            self.df['CL_filt'] = self.df['CL_active'].rolling(window=window, min_periods=1, center=True).mean()
            self.df['CD_filt'] = self.df['CD_active'].rolling(window=window, min_periods=1, center=True).mean()
        elif "指数移動平均" in method:
            self.df['CL_filt'] = self.df['CL_active'].ewm(span=window, adjust=False).mean()
            self.df['CD_filt'] = self.df['CD_active'].ewm(span=window, adjust=False).mean()
        else: # なし
            self.df['CL_filt'] = self.df['CL_active']
            self.df['CD_filt'] = self.df['CD_active']

        # 描画の反映
        t = self.df['Time_s'].values
        
        # 1. 空力係数
        self.curve_cl_raw.setData(t, self.df['CL_active'].values)
        self.curve_cl_filt.setData(t, self.df['CL_filt'].values)
        self.curve_cd_raw.setData(t, self.df['CD_active'].values)
        self.curve_cd_filt.setData(t, self.df['CD_filt'].values)
        self.curve_wind.setData(t, pd.to_numeric(self.df['AvgWind']).values)

        # 2. 6分力モニター
        self.curve_fx.setData(t, self.df['Fx_net'].values)
        self.curve_fy.setData(t, self.df['Fy_net'].values)
        self.curve_fz.setData(t, self.df['Fz_net'].values)
        self.curve_mx.setData(t, self.df['Mx_net'].values)
        self.curve_my.setData(t, self.df['My_net'].values)
        self.curve_mz.setData(t, self.df['Mz_net'].values)

        # 3. 風圧中心 (CoP)
        self.curve_cop_z.setData(t, self.df['CoP_Z'].values)
        self.curve_cop_x.setData(t, self.df['CoP_X'].values)

        self.update_analysis_plots()

    # ==================== 統計＆特定範囲分析 ====================
    def update_analysis_plots(self):
        if self.df is None:
            return

        t_min, t_max = self.region.getRegion()
        mask = (self.df['Time_s'] >= t_min) & (self.df['Time_s'] <= t_max)
        sub_df = self.df[mask]
        num_pts = len(sub_df)

        self.lbl_region_info.setText(f"選択範囲: {t_min:.2f}秒 〜 {t_max:.2f}秒 ({num_pts}点)")

        if num_pts < 4:
            return

        # 統計レポート表の構築
        vars_to_show = [
            ("CL (Filtered)", sub_df['CL_filt']),
            ("CD (Filtered)", sub_df['CD_filt']),
            ("Fx_net (N)", sub_df['Fx_net']),
            ("Fy_net (N)", sub_df['Fy_net']),
            ("Mz_net (Nm)", sub_df['Mz_net']),
            ("CoP_Z (Height m)", sub_df['CoP_Z'])
        ]

        for row, (name, series) in enumerate(vars_to_show):
            valid = series.dropna()
            if len(valid) == 0:
                continue
            mean_v = valid.mean()
            std_v = valid.std()
            rms_v = np.sqrt(np.mean(valid**2))
            max_v = valid.max()
            min_v = valid.min()

            self.table_stats.setItem(row, 0, QTableWidgetItem(name))
            self.table_stats.setItem(row, 1, QTableWidgetItem(f"{mean_v:.4f}"))
            self.table_stats.setItem(row, 2, QTableWidgetItem(f"{std_v:.4f}"))
            self.table_stats.setItem(row, 3, QTableWidgetItem(f"{rms_v:.4f}"))
            self.table_stats.setItem(row, 4, QTableWidgetItem(f"{max_v:.3f} / {min_v:.3f}"))

        # --- Tab3: CoP 2D 断面マッピングプロット ---
        x_cop_vals = sub_df['CoP_X'].values
        z_cop_vals = sub_df['CoP_Z'].values
        
        # 点数が多い場合に間引き
        if len(x_cop_vals) > 1000:
            step = len(x_cop_vals) // 1000
            x_plot = x_cop_vals[::step]
            z_plot = z_cop_vals[::step]
        else:
            x_plot = x_cop_vals
            z_plot = z_cop_vals

        self.scatter_cop.setData(x=x_plot, y=z_plot)
        self.scatter_cop_avg.setData(x=[np.mean(x_cop_vals)], y=[np.mean(z_cop_vals)])

        # --- Tab4: FFT ＆ ヒストグラムの計算 ---
        target_var = self.combo_var.currentText()
        if target_var == "CL":
            y_data = sub_df['CL_filt'].values
        elif target_var == "CD":
            y_data = sub_df['CD_filt'].values
        elif "Fx" in target_var:
            y_data = sub_df['Fx_net'].values
        elif "Fy" in target_var:
            y_data = sub_df['Fy_net'].values
        elif "Mz" in target_var:
            y_data = sub_df['Mz_net'].values
        else:
            y_data = sub_df['CoP_Z'].values

        times = sub_df['Time_s'].values
        dt = np.mean(np.diff(times)) if len(times) > 1 else 0.1

        # FFT
        if dt > 0:
            y_detrend = y_data - np.mean(y_data) # 平均値の除去
            n = len(y_detrend)
            yf = np.fft.rfft(y_detrend)
            xf = np.fft.rfftfreq(n, d=dt)
            amp = (2.0 / n) * np.abs(yf)

            self.curve_fft.setData(xf, amp)
            self.plot_fft.setTitle(f"【FFT】{target_var} の周波数振幅")

            # ピーク値の自動検出
            if len(amp) > 2:
                peak_idx = np.argmax(amp[1:]) + 1
                peak_f = xf[peak_idx]
                peak_a = amp[peak_idx]
                self.v_line_fft.setValue(peak_f)
                self.lbl_peak_freq.setText(f"ピーク周波数: {peak_f:.3f} Hz (振幅: {peak_a:.5f})")
            else:
                self.lbl_peak_freq.setText("ピーク周波数: N/A")
        else:
            self.curve_fft.setData([], [])
            self.lbl_peak_freq.setText("ピーク周波数: N/A")

        # ヒストグラム
        counts, bins = np.histogram(y_data, bins='auto')
        bin_centers = 0.5 * (bins[:-1] + bins[1:])
        widths = np.diff(bins)
        self.hist_item.setOpts(x=bin_centers, height=counts, width=widths, brush=pg.mkBrush('#3498db'), pen='k')
        self.plot_hist.setTitle(f"【分布】{target_var} のヒストグラム")

    # ==================== データ保存 ====================
    def get_selected_dataframe(self):
        if self.df is None:
            return None
        t_min, t_max = self.region.getRegion()
        mask = (self.df['Time_s'] >= t_min) & (self.df['Time_s'] <= t_max)
        return self.df[mask].copy()

    def export_region_data(self):
        sub_df = self.get_selected_dataframe()
        if sub_df is None or len(sub_df) == 0:
            QMessageBox.information(self, "情報", "書き出し対象の範囲データが選択されていません。")
            return

        filepath, _ = QFileDialog.getSaveFileName(self, "CSVデータ書き出し", "selected_range_extract.csv", "CSV Files (*.csv)")
        if filepath:
            try:
                sub_df.to_csv(filepath, index=False)
                QMessageBox.information(self, "成功", "選択した範囲データの書き出しに成功しました！")
            except Exception as e:
                QMessageBox.critical(self, "エラー", f"書き出し失敗:\n{e}")

    def export_stats_summary(self):
        if self.df is None:
            return
            
        filepath, _ = QFileDialog.getSaveFileName(self, "統計レポートの保存", "stats_summary_report.txt", "Text Files (*.txt)")
        if filepath:
            try:
                t_min, t_max = self.region.getRegion()
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write("=== 風洞試験 選択窓内解析・統計サマリーレポート ===\n\n")
                    f.write(f"解析対象ファイル: {self.lbl_test_file.text()}\n")
                    f.write(f"解析時間幅: {t_min:.2f} s 〜 {t_max:.2f} s\n")
                    f.write(f"ヘッダーメモ: {self.lbl_meta_memo.text()}\n")
                    f.write(f"適用代表面積 S: {self.input_area.text()} m^2\n")
                    f.write(f"Tareオフセット補正: {'有効' if self.cb_recalc.isChecked() else '無効'}\n\n")
                    
                    f.write(f"{'変数':<25} | {'平均値':<10} | {'標準偏差':<10} | {'RMS値':<10} | {'最大 / 最小':<20}\n")
                    f.write("-" * 85 + "\n")
                    for row in range(self.table_stats.rowCount()):
                        v = [self.table_stats.item(row, col).text() if self.table_stats.item(row, col) else "" for col in range(5)]
                        f.write(f"{v[0]:<25} | {v[1]:<10} | {v[2]:<10} | {v[3]:<10} | {v[4]:<20}\n")
                
                QMessageBox.information(self, "成功", "統計サマリーの保存に成功しました！")
            except Exception as e:
                QMessageBox.critical(self, "エラー", f"サマリー保存に失敗しました:\n{e}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = TimeSeriesAnalyzer()
    window.show()
    sys.exit(app.exec())
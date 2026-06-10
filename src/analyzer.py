import sys
import pandas as pd
import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QPushButton, QLabel, QFileDialog, 
                               QMessageBox, QFormLayout, QLineEdit, QGroupBox,
                               QSpinBox, QTableWidget, QTableWidgetItem, QHeaderView)
from PySide6.QtCore import Qt

class WindTunnelAnalyzer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Wind Tunnel Data Analyzer (Discrete Point Mode)")
        self.resize(1300, 900)

        # 解析結果を貯めるデータフレーム
        self.results_df = pd.DataFrame(columns=['Angle', 'CL', 'CD', 'L_D', 'AvgWind', 'AvgRho', 'Net_Fy', 'Net_Fx'])
        
        self.current_tare_path = ""
        self.current_test_path = ""

        self.setup_ui()

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)

        # === 左パネル (コントロールとデータテーブル) ===
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_panel.setFixedWidth(400)

        # 1. パラメータ設定
        group_params = QGroupBox("1. Setup")
        form_params = QFormLayout()
        self.input_area = QLineEdit("0.25")
        form_params.addRow("Sail Area [m²]:", self.input_area)
        group_params.setLayout(form_params)
        left_layout.addWidget(group_params)

        # 2. データポイントの追加
        group_add = QGroupBox("2. Add Data Point")
        vbox_add = QVBoxLayout()
        
        form_add = QFormLayout()
        self.spin_angle = QSpinBox()
        self.spin_angle.setRange(-180, 180)
        self.spin_angle.setValue(0)
        self.spin_angle.setSuffix(" deg")
        form_add.addRow("Target Angle:", self.spin_angle)
        vbox_add.addLayout(form_add)

        self.btn_tare = QPushButton("Select Tare CSV (Wind = 0)")
        self.btn_tare.clicked.connect(self.select_tare)
        self.lbl_tare = QLabel("No file selected")
        self.lbl_tare.setStyleSheet("color: gray; font-size: 10px;")
        vbox_add.addWidget(self.btn_tare)
        vbox_add.addWidget(self.lbl_tare)

        self.btn_test = QPushButton("Select Test CSV (Wind > 0)")
        self.btn_test.clicked.connect(self.select_test)
        self.lbl_test = QLabel("No file selected")
        self.lbl_test.setStyleSheet("color: gray; font-size: 10px;")
        vbox_add.addWidget(self.btn_test)
        vbox_add.addWidget(self.lbl_test)

        self.btn_calc = QPushButton("Calculate & Add Point")
        self.btn_calc.setStyleSheet("background-color: #28a745; color: white; font-weight: bold; padding: 10px; margin-top: 10px;")
        self.btn_calc.clicked.connect(self.calculate_and_add)
        vbox_add.addWidget(self.btn_calc)

        group_add.setLayout(vbox_add)
        left_layout.addWidget(group_add)

        # 3. 解析結果のリスト (テーブル)
        group_table = QGroupBox("3. Processed Data Points")
        vbox_table = QVBoxLayout()
        
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Angle", "CL", "CD", "L/D"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        vbox_table.addWidget(self.table)

        hbox_table_btns = QHBoxLayout()
        self.btn_clear = QPushButton("Clear All")
        self.btn_clear.clicked.connect(self.clear_data)
        self.btn_export = QPushButton("Export Summary CSV")
        self.btn_export.setStyleSheet("background-color: #007bff; color: white; font-weight: bold;")
        self.btn_export.clicked.connect(self.export_csv)
        hbox_table_btns.addWidget(self.btn_clear)
        hbox_table_btns.addWidget(self.btn_export)
        vbox_table.addLayout(hbox_table_btns)

        group_table.setLayout(vbox_table)
        left_layout.addWidget(group_table)

        # === 右パネル (グラフ群) ===
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
        pg.setConfigOption('background', 'w')
        pg.setConfigOption('foreground', 'k')

        # グラフ1: CL vs Angle
        self.plot_cl = pg.PlotWidget(title="Lift Curve (CL vs Angle)")
        self.plot_cl.showGrid(x=True, y=True)
        self.plot_cl.setLabel('bottom', 'Angle [deg]')
        self.plot_cl.setLabel('left', 'Lift Coefficient (CL)')
        self.curve_cl = self.plot_cl.plot(pen=pg.mkPen('r', width=2), symbol='o', symbolBrush='r', symbolSize=8)
        right_layout.addWidget(self.plot_cl)

        # グラフ2: CD vs Angle
        self.plot_cd = pg.PlotWidget(title="Drag Curve (CD vs Angle)")
        self.plot_cd.showGrid(x=True, y=True)
        self.plot_cd.setLabel('bottom', 'Angle [deg]')
        self.plot_cd.setLabel('left', 'Drag Coefficient (CD)')
        self.curve_cd = self.plot_cd.plot(pen=pg.mkPen('b', width=2), symbol='o', symbolBrush='b', symbolSize=8)
        right_layout.addWidget(self.plot_cd)

        # グラフ3: Polar Curve
        self.plot_polar = pg.PlotWidget(title="Drag Polar (CL vs CD)")
        self.plot_polar.showGrid(x=True, y=True)
        self.plot_polar.setLabel('bottom', 'Drag Coefficient (CD)')
        self.plot_polar.setLabel('left', 'Lift Coefficient (CL)')
        self.curve_polar = self.plot_polar.plot(pen=pg.mkPen('g', width=2), symbol='s', symbolBrush='g', symbolSize=8)
        right_layout.addWidget(self.plot_polar)

        main_layout.addWidget(left_panel)
        main_layout.addWidget(right_panel, stretch=1)

    # --- ヘルパー関数群 ---
    def _read_csv_safe(self, filepath):
        try:
            return pd.read_csv(filepath, comment='#', encoding='utf-8')
        except UnicodeDecodeError:
            return pd.read_csv(filepath, comment='#', encoding='cp932')

    def _clean_dataframe_columns(self, df):
        df.columns = df.columns.str.strip()
        df.columns = df.columns.str.strip("'")
        df.columns = df.columns.str.strip('"')
        return df

    def _get_mean_values(self, filepath):
        df = self._read_csv_safe(filepath)
        df = self._clean_dataframe_columns(df)
        
        forces = ['Fx(N)', 'Fy(N)', 'Fz(N)', 'Temperature', 'Humidity', 'Pressure', 'AvgWind']
        missing = [col for col in forces if col not in df.columns]
        if missing:
            raise ValueError(f"Missing columns: {missing}")

        for col in forces:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        
        df = df.dropna(subset=forces)
        if len(df) == 0:
            raise ValueError("No valid numeric data found in CSV.")

        return df[forces].mean()

    # --- UIアクション ---
    def select_tare(self):
        filepath, _ = QFileDialog.getOpenFileName(self, "Select Tare CSV", "", "CSV Files (*.csv)")
        if filepath:
            self.current_tare_path = filepath
            filename = filepath.split('/')[-1] if '/' in filepath else filepath.split('\\')[-1]
            self.lbl_tare.setText(filename)

    def select_test(self):
        filepath, _ = QFileDialog.getOpenFileName(self, "Select Test CSV", "", "CSV Files (*.csv)")
        if filepath:
            self.current_test_path = filepath
            filename = filepath.split('/')[-1] if '/' in filepath else filepath.split('\\')[-1]
            self.lbl_test.setText(filename)

    def calculate_and_add(self):
        if not self.current_tare_path or not self.current_test_path:
            QMessageBox.warning(self, "Warning", "Please select BOTH Tare and Test CSV files.")
            return

        try:
            area = float(self.input_area.text())
            angle = float(self.spin_angle.value())

            if angle in self.results_df['Angle'].values:
                reply = QMessageBox.question(self, "Overwrite?", f"Data for {angle} deg already exists. Overwrite?",
                                             QMessageBox.Yes | QMessageBox.No)
                if reply == QMessageBox.No:
                    return
                self.results_df = self.results_df[self.results_df['Angle'] != angle]

            # 各ファイルの平均値を算出
            tare_means = self._get_mean_values(self.current_tare_path)
            test_means = self._get_mean_values(self.current_test_path)

            # 空力(Tare引き算)
            net_fx = test_means['Fx(N)'] - tare_means['Fx(N)']
            net_fy = test_means['Fy(N)'] - tare_means['Fy(N)']

            # 動圧の計算
            Tk = test_means['Temperature'] + 273.15
            Es = 6.1078 * 10.0 ** ((7.5 * test_means['Temperature']) / (test_means['Temperature'] + 237.3))
            Pv = Es * (test_means['Humidity'] / 100.0)
            Pd = test_means['Pressure'] - Pv
            rho = (Pd * 100.0 / (287.05 * Tk)) + (Pv * 100.0 / (461.495 * Tk))
            
            q = 0.5 * rho * (test_means['AvgWind'] ** 2)

            if q < 0.5:
                QMessageBox.warning(self, "Warning", "Wind speed is too low (q < 0.5 Pa). Cannot calculate coefficients accurately.")
                return

            # 係数の計算
            cl = net_fy / (q * area)
            cd = -net_fx / (q * area)
            l_d = cl / cd if cd != 0 else 0

            # 新しい行の作成
            new_row = pd.DataFrame([{
                'Angle': angle, 'CL': cl, 'CD': cd, 'L_D': l_d, 
                'AvgWind': test_means['AvgWind'], 'AvgRho': rho,
                'Net_Fy': net_fy, 'Net_Fx': net_fx
            }])
            
            self.results_df = pd.concat([self.results_df, new_row], ignore_index=True)
            
            # ★ 修正ポイント 1: データフレーム全体を明示的に float 型に変換し、object型が混入するのを防ぐ
            self.results_df = self.results_df.astype(float)
            
            self.results_df = self.results_df.sort_values(by='Angle').reset_index(drop=True)

            # UIの更新
            self.update_ui()
            
            # 入力を次に備えてクリア (角度を自動で1度進める)
            self.spin_angle.setValue(int(angle) + 1)
            self.current_tare_path = ""
            self.current_test_path = ""
            self.lbl_tare.setText("No file selected")
            self.lbl_test.setText("No file selected")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Calculation failed:\n{str(e)}")

    def update_ui(self):
        # テーブルの更新
        self.table.setRowCount(len(self.results_df))
        for row, idx in enumerate(self.results_df.index):
            self.table.setItem(row, 0, QTableWidgetItem(f"{self.results_df.loc[idx, 'Angle']:.0f}"))
            self.table.setItem(row, 1, QTableWidgetItem(f"{self.results_df.loc[idx, 'CL']:.4f}"))
            self.table.setItem(row, 2, QTableWidgetItem(f"{self.results_df.loc[idx, 'CD']:.4f}"))
            self.table.setItem(row, 3, QTableWidgetItem(f"{self.results_df.loc[idx, 'L_D']:.2f}"))

        # ★ 修正ポイント 2: グラフに渡すデータを完全に純粋なNumpyのfloat配列に強制変換する
        angles = np.array(self.results_df['Angle'].values, dtype=float)
        cls = np.array(self.results_df['CL'].values, dtype=float)
        cds = np.array(self.results_df['CD'].values, dtype=float)

        self.curve_cl.setData(x=angles, y=cls)
        self.curve_cd.setData(x=angles, y=cds)
        self.curve_polar.setData(x=cds, y=cls)

    def clear_data(self):
        reply = QMessageBox.question(self, "Clear All", "Are you sure you want to clear all data points?",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.results_df = pd.DataFrame(columns=['Angle', 'CL', 'CD', 'L_D', 'AvgWind', 'AvgRho', 'Net_Fy', 'Net_Fx'])
            self.update_ui()

    def export_csv(self):
        if len(self.results_df) == 0:
            QMessageBox.information(self, "Info", "No data to export.")
            return
            
        filepath, _ = QFileDialog.getSaveFileName(self, "Save Summary CSV", "wind_tunnel_summary.csv", "CSV Files (*.csv)")
        if filepath:
            try:
                self.results_df.to_csv(filepath, index=False)
                QMessageBox.information(self, "Success", "Summary exported successfully!")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to export:\n{e}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = WindTunnelAnalyzer()
    window.show()
    sys.exit(app.exec())
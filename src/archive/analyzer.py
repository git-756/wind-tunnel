import sys
import os
import glob
import pandas as pd
import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QPushButton, QLabel, QFileDialog, 
                               QMessageBox, QFormLayout, QLineEdit, QGroupBox,
                               QTableWidget, QTableWidgetItem, QHeaderView)
from PySide6.QtCore import Qt

class WindTunnelAnalyzer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Wind Tunnel Data Analyzer (Batch Folder Mode)")
        self.resize(1300, 900)

        # 解析結果を貯めるデータフレーム
        self.results_df = pd.DataFrame(columns=['Angle', 'CL', 'CD', 'L_D', 'AvgWind', 'AvgRho', 'Net_Fy', 'Net_Fx'])

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
        self.input_area = QLineEdit("0.1875") # 前回のデフォルト値に合わせました
        form_params.addRow("Sail Area [m²]:", self.input_area)
        group_params.setLayout(form_params)
        left_layout.addWidget(group_params)

        # 2. フォルダ一括自動解析
        group_batch = QGroupBox("2. Batch Directory Analysis")
        vbox_batch = QVBoxLayout()
        
        self.btn_analyze_dir = QPushButton("Select Folder & Analyze")
        self.btn_analyze_dir.setStyleSheet("background-color: #28a745; color: white; font-weight: bold; padding: 12px;")
        self.btn_analyze_dir.clicked.connect(self.select_and_analyze_dir)
        vbox_batch.addWidget(self.btn_analyze_dir)

        self.lbl_status_info = QLabel("Filename rule:\n..._[Tare/test]_[Angle].csv")
        self.lbl_status_info.setStyleSheet("color: gray; font-size: 11px;")
        vbox_batch.addWidget(self.lbl_status_info)

        group_batch.setLayout(vbox_batch)
        left_layout.addWidget(group_batch)

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

    # --- UIアクション (フォルダ一括解析) ---
    def select_and_analyze_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select Data Directory", "")
        if not dir_path:
            return

        # すでにデータがある場合は上書き確認
        if len(self.results_df) > 0:
            reply = QMessageBox.question(self, "Clear Data?", "Clear currently loaded data before batch analysis?",
                                         QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.results_df = pd.DataFrame(columns=['Angle', 'CL', 'CD', 'L_D', 'AvgWind', 'AvgRho', 'Net_Fy', 'Net_Fx'])

        csv_files = glob.glob(os.path.join(dir_path, "*.csv"))
        if not csv_files:
            QMessageBox.warning(self, "No Files", "No CSV files found in the selected folder.")
            return

        # 角度ごとにTareとTestをマッピングする辞書を構築
        # 構造: { angle(float): {'tare': filepath, 'test': filepath} }
        pair_map = {}

        for filepath in csv_files:
            filename = os.path.basename(filepath)
            name_we, _ = os.path.splitext(filename)
            parts = name_we.split('_')
            
            # 命名規則「..._[シーン]_[角度]」を満たすため最低3要素必要
            if len(parts) < 3:
                continue 
            
            try:
                angle_str = parts[-1]        # 末尾：角度
                scene_str = parts[-2].lower() # その前：シーン
                
                angle = float(angle_str)
                
                if 'tare' in scene_str:
                    scene = 'tare'
                elif 'test' in scene_str:
                    scene = 'test'
                else:
                    continue # どちらでもない場合はスキップ
                
                if angle not in pair_map:
                    pair_map[angle] = {'tare': None, 'test': None}
                
                pair_map[angle][scene] = filepath
                
            except ValueError:
                continue # 角度への変換失敗時はスキップ

        # パラメータ取得
        try:
            area = float(self.input_area.text())
        except ValueError:
            QMessageBox.warning(self, "Warning", "Invalid Sail Area value.")
            return

        success_count = 0
        skipped_angles = []
        new_rows = []

        # ペアリングされたデータを順次解析
        for angle, pairs in pair_map.items():
            tare_path = pairs['tare']
            test_path = pairs['test']
            
            # TareとTestが揃っていない角度はスキップ
            if not tare_path or not test_path:
                skipped_angles.append(angle)
                continue
                
            try:
                tare_means = self._get_mean_values(tare_path)
                test_means = self._get_mean_values(test_path)

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
                    continue # 風速不足時はスキップ

                # 係数の計算
                cl = net_fy / (q * area)
                cd = net_fx / (q * area) # ★ 符号を修正（マイナスを除去）
                l_d = cl / cd if cd != 0 else 0

                new_rows.append({
                    'Angle': angle, 'CL': cl, 'CD': cd, 'L_D': l_d, 
                    'AvgWind': test_means['AvgWind'], 'AvgRho': rho,
                    'Net_Fy': net_fy, 'Net_Fx': net_fx
                })
                success_count += 1
            except Exception as e:
                print(f"Failed to process angle {angle}: {e}")
                continue

        if new_rows:
            new_df = pd.DataFrame(new_rows)
            # 重複する古い角度データを削除して結合
            if len(self.results_df) > 0:
                self.results_df = self.results_df[~self.results_df['Angle'].isin(new_df['Angle'])]
            
            self.results_df = pd.concat([self.results_df, new_df], ignore_index=True)
            self.results_df = self.results_df.astype(float)
            self.results_df = self.results_df.sort_values(by='Angle').reset_index(drop=True)
            
            # UI更新
            self.update_ui()
            
            # 完了通知文の作成
            msg = f"Successfully processed {success_count} data points."
            if skipped_angles:
                msg += f"\n\n[Skipped Incomplete Angles]\n(Missing Tare or Test): {sorted(skipped_angles)}"
            QMessageBox.information(self, "Analysis Complete", msg)
        else:
            QMessageBox.warning(self, "No Valid Data", "No valid Tare/Test pairs found in the selected folder.")

    def update_ui(self):
        # テーブルの更新
        self.table.setRowCount(len(self.results_df))
        for row, idx in enumerate(self.results_df.index):
            self.table.setItem(row, 0, QTableWidgetItem(f"{self.results_df.loc[idx, 'Angle']:.1f}"))
            self.table.setItem(row, 1, QTableWidgetItem(f"{self.results_df.loc[idx, 'CL']:.4f}"))
            self.table.setItem(row, 2, QTableWidgetItem(f"{self.results_df.loc[idx, 'CD']:.4f}"))
            self.table.setItem(row, 3, QTableWidgetItem(f"{self.results_df.loc[idx, 'L_D']:.2f}"))

        # グラフデータの同期
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
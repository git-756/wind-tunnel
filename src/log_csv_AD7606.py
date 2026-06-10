import sys
import os
import csv
import serial
import serial.tools.list_ports
import numpy as np
import pyqtgraph as pg
from datetime import datetime
from collections import deque

from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                               QGroupBox, QFormLayout, QMessageBox, QComboBox,
                               QSpinBox)
from PySide6.QtCore import QThread, Signal, Slot, Qt, QTimer

SAIL_AREA = 0.25 

CALIB_MATRIX = np.array([
    [0.03259, 0.04063, 0.16369, 12.41458, -0.17227, -12.09472],
    [0.06766, -14.48259, 0.17097, 7.06294, 0.1743, 7.10026],
    [20.94395, 0.10918, 20.82951, 0.1812, 21.38518, -0.04675],
    [-0.00248, -0.07339, 0.29812, 0.03697, -0.29826, 0.03658],
    [-0.33697, -0.00357, 0.17214, -0.05776, 0.17193, 0.05886],
    [0.00147, -0.17701, -0.00125, -0.17339, -0.0026, -0.16894]
])

GRAPH_POINTS = 200 
UI_MA_WINDOW = 10 

BAUD_RATE = 115200
SAVE_DIR = "result_csv"

# ★ フィードバックのパラメータを最適化
FEEDBACK_TOLERANCE_STRICT = 0.15  
FEEDBACK_TOLERANCE_FINAL = 0.25   
FEEDBACK_INTERVAL = 3000         
MAX_ITERATIONS = 20              # タイムアウトしにくいように20回に増加
FEEDBACK_DAMPING = 0.9           # 0.9へ変更。より積極的に誤差を詰める

class SerialWorker(QThread):
    data_received = Signal(float, float, float, float, float, object, object, float)
    connection_success = Signal()
    connection_error = Signal(str)

    def __init__(self, port_name):
        super().__init__()
        self.port_name = port_name
        self.is_running = True
        self.is_recording = False
        self.ser = None
        self.csv_file = None
        self.csv_writer = None

    def set_recording(self, state, memo="", sail_area="", duration_str="", avg_wind=0.0):
        self.is_recording = state
        if state:
            os.makedirs(SAVE_DIR, exist_ok=True)
            
            # 平均風速によるサフィックスの決定
            suffix = ""
            if avg_wind <= 0.3:
                suffix = "_Tare"
            elif avg_wind >= 4.0:
                suffix = "_test"
                
            filename = datetime.now().strftime('%Y-%m-%d-%H-%M-%S') + suffix + ".csv"
            filepath = os.path.join(SAVE_DIR, filename)
            
            self.csv_file = open(filepath, 'w', newline='')
            self.csv_writer = csv.writer(self.csv_file)
            
            self.csv_writer.writerow(["# --- Wind Tunnel Test Metadata ---"])
            self.csv_writer.writerow(["# Memo:", memo])
            self.csv_writer.writerow(["# Sail Area [m^2]:", sail_area])
            self.csv_writer.writerow(["# Timer Setting:", duration_str])
            self.csv_writer.writerow([]) 
            
            header = ["Timestamp", "Temperature", "Humidity", "Pressure", 
                      "CH1(V)", "CH2(V)", "CH3(V)", "CH4(V)", "CH5(V)", "CH6(V)", 
                      "Fx(N)", "Fy(N)", "Fz(N)", "F_Total(N)", "Mx(Nm)", "My(Nm)", "Mz(Nm)",
                      "PotDegrees", "AvgWind", "CL", "CD"]
            self.csv_writer.writerow(header)
            print(f"CSV Recording Started: {filename}")
        else:
            if self.csv_file:
                self.csv_file.close()
                self.csv_file = None
                self.csv_writer = None
            print("CSV Recording Stopped")

    def run(self):
        try:
            self.ser = serial.Serial(self.port_name, BAUD_RATE, timeout=1)
            print(f"Connected to {self.port_name}")
            self.connection_success.emit()

            while self.is_running:
                if self.ser.in_waiting > 0:
                    try:
                        line = self.ser.readline().decode('utf-8').strip()
                        if line and "," in line:
                            parts = line.split(',')
                            if len(parts) >= 11:
                                temp, hum, pres = float(parts[0]), float(parts[1]), float(parts[2])
                                pot, wind = float(parts[9]), float(parts[10])
                                
                                sg_array = np.array([float(parts[3]), float(parts[4]), float(parts[5]), 
                                                     float(parts[6]), float(parts[7]), float(parts[8])])
                                
                                fm_array = np.dot(CALIB_MATRIX, sg_array)
                                fx, fy, fz, mx, my, mz = fm_array
                                f_val = np.sqrt(fx**2 + fy**2 + fz**2)

                                self.data_received.emit(temp, hum, pres, pot, wind, sg_array, fm_array, f_val)

                                if self.is_recording and self.csv_writer:
                                    if not np.isnan(temp) and not np.isnan(hum) and not np.isnan(pres):
                                        Tk = temp + 273.15
                                        if Tk > 0:
                                            Es = 6.1078 * 10.0 ** ((7.5 * temp) / (temp + 237.3))
                                            Pv = Es * (hum / 100.0)
                                            Pd = pres - Pv
                                            rho = (Pd * 100.0 / (287.05 * Tk)) + (Pv * 100.0 / (461.495 * Tk))
                                        else:
                                            rho = np.nan
                                    else:
                                        rho = np.nan
                                    
                                    q = 0.5 * rho * (wind ** 2) if not np.isnan(rho) and not np.isnan(wind) else np.nan
                                    
                                    if not np.isnan(q) and q > 0.5:
                                        cl_csv = fy / (q * SAIL_AREA)
                                        cd_csv = -fx / (q * SAIL_AREA)
                                    else:
                                        cl_csv = 0.0
                                        cd_csv = 0.0

                                    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                                    row = [now_str, temp, hum, pres, 
                                           *sg_array.tolist(), fx, fy, fz, f_val, mx, my, mz, 
                                           pot, wind, cl_csv, cd_csv]
                                    self.csv_writer.writerow(row)
                                    self.csv_file.flush()
                                    
                    except ValueError:
                        pass
                    except Exception as e:
                        print(f"Error reading line: {e}")
                self.msleep(2)

        except Exception as e:
            self.connection_error.emit(str(e))
        finally:
            if self.csv_file:
                self.csv_file.close()
            if self.ser and self.ser.is_open:
                self.ser.close()

    def send_servo_angle(self, angle):
        if self.ser and self.ser.is_open:
            try:
                self.ser.write(f"{angle:.2f}\n".encode('utf-8'))
            except Exception as e:
                print(f"Send Error: {e}")

    def stop(self):
        self.is_running = False
        self.wait()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Wind Tunnel Monitor & Aerodynamics Graph")
        self.resize(1400, 1000)

        self.time_idx = 0
        self.x_data = deque(maxlen=GRAPH_POINTS)
        self.fx_data = deque(maxlen=GRAPH_POINTS)
        self.fy_data = deque(maxlen=GRAPH_POINTS)
        self.fz_data = deque(maxlen=GRAPH_POINTS)
        self.f_val_data = deque(maxlen=GRAPH_POINTS) 
        self.mx_data = deque(maxlen=GRAPH_POINTS)
        self.my_data = deque(maxlen=GRAPH_POINTS)
        self.mz_data = deque(maxlen=GRAPH_POINTS)
        self.cl_data = deque(maxlen=GRAPH_POINTS) 
        self.cd_data = deque(maxlen=GRAPH_POINTS) 
        
        self.wind_data = deque(maxlen=GRAPH_POINTS)
        self.pot_data = deque(maxlen=GRAPH_POINTS)
        self.temp_data = deque(maxlen=GRAPH_POINTS)
        self.hum_data = deque(maxlen=GRAPH_POINTS)
        self.pres_data = deque(maxlen=GRAPH_POINTS)
        self.rho_data = deque(maxlen=GRAPH_POINTS)
        self.q_data = deque(maxlen=GRAPH_POINTS)
        
        self.sg_data = [deque(maxlen=UI_MA_WINDOW) for _ in range(6)]

        self.pot_offset = 0.0          
        self.fm_offset = np.zeros(6)
        self.latest_fm_raw = np.zeros(6)

        self.last_servo_command = 90.0   
        self.servo_center_val = 90.0     
        self.feedback_target = 0.0
        self.feedback_iteration = 0
        
        # ★ 状態管理用フラグ・変数
        self.feedback_state = 'ROUGH_MOVING'
        self.rough_move_timeout = 0
        self.rough_stop_count = 0
        self.prev_pot_display = None
        
        self.feedback_timer = QTimer()
        self.feedback_timer.timeout.connect(self.process_feedback_loop)

        self.record_start_time = None
        self.ui_timer = QTimer()
        self.ui_timer.timeout.connect(self.update_ui_elements)
        self.ui_timer.start(100)

        self.worker = None
        self.setup_ui()

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_panel.setFixedWidth(420)

        conn_layout = QHBoxLayout()
        self.port_combo = QComboBox()
        self.refresh_ports()
        conn_layout.addWidget(QLabel("Port:"))
        conn_layout.addWidget(self.port_combo)
        self.btn_connect = QPushButton("Connect")
        self.btn_connect.clicked.connect(self.toggle_connection)
        conn_layout.addWidget(self.btn_connect)
        left_layout.addLayout(conn_layout)

        log_group = QGroupBox("Data Logging")
        log_layout = QVBoxLayout()

        status_layout = QHBoxLayout()
        self.lbl_rec_status = QLabel("Standby")
        self.lbl_rec_status.setStyleSheet("color: gray; font-weight: bold; font-size: 14px;")
        
        self.lbl_elapsed = QLabel("0.0 s")
        self.lbl_elapsed.setStyleSheet("color: black; font-weight: bold; font-size: 16px;")
        self.lbl_elapsed.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        
        status_layout.addWidget(self.lbl_rec_status)
        status_layout.addWidget(self.lbl_elapsed)
        log_layout.addLayout(status_layout)

        timer_layout = QHBoxLayout()
        self.btn_timer_toggle = QPushButton("Timer: OFF")
        self.btn_timer_toggle.setCheckable(True)
        self.btn_timer_toggle.setStyleSheet("background-color: #e0e0e0; color: black; font-weight: bold; padding: 5px;")
        self.btn_timer_toggle.clicked.connect(self.toggle_timer_mode)

        self.spin_duration = QSpinBox()
        self.spin_duration.setRange(1, 36000)
        self.spin_duration.setValue(60)       
        self.spin_duration.setSuffix(" s")
        self.spin_duration.setEnabled(False)  

        timer_layout.addWidget(self.btn_timer_toggle)
        timer_layout.addWidget(QLabel("Duration:"))
        timer_layout.addWidget(self.spin_duration)
        log_layout.addLayout(timer_layout)

        memo_layout = QHBoxLayout()
        memo_layout.addWidget(QLabel("Memo:"))
        self.input_memo = QLineEdit()
        self.input_memo.setPlaceholderText("Test description, conditions, etc.")
        memo_layout.addWidget(self.input_memo)
        log_layout.addLayout(memo_layout)

        self.btn_rec = QPushButton("Start REC (OFF)")
        self.btn_rec.setCheckable(True)
        self.btn_rec.setStyleSheet("background-color: #e0e0e0; color: black; font-weight: bold; padding: 10px;")
        self.btn_rec.clicked.connect(self.toggle_recording)
        self.btn_rec.setEnabled(False)
        log_layout.addWidget(self.btn_rec)

        log_group.setLayout(log_layout)
        left_layout.addWidget(log_group)

        sensor_group = QGroupBox(f"Real-time UI (0.5s Avg) / Graph (20Hz Raw)")
        form_layout = QFormLayout()
        font = self.font()
        font.setPointSize(12)
        font.setBold(True)

        self.lbl_temp = QLabel("--.- °C")
        self.lbl_hum = QLabel("--.- %")
        self.lbl_pres = QLabel("--.- hPa")
        form_layout.addRow("Temp:", self.lbl_temp)
        form_layout.addRow("Humidity:", self.lbl_hum)
        form_layout.addRow("Pressure:", self.lbl_pres)

        self.lbl_rho = QLabel("--.--- kg/m³")
        self.lbl_q = QLabel("--.- Pa")
        form_layout.addRow("Air Density (ρ):", self.lbl_rho)
        form_layout.addRow("Dyn. Pressure (q):", self.lbl_q)

        self.lbl_wind = QLabel("--.- m/s")
        self.lbl_pot = QLabel("--.- deg")
        self.btn_zero_pot = QPushButton("Set 0°")
        self.btn_zero_pot.clicked.connect(self.set_zero_pot)
        pot_layout = QHBoxLayout()
        pot_layout.addWidget(self.lbl_pot)
        pot_layout.addWidget(self.btn_zero_pot)
        form_layout.addRow("Wind Speed:", self.lbl_wind)
        form_layout.addRow("Rotation:", pot_layout)
            
        tare_layout = QHBoxLayout()
        self.btn_tare_toggle = QPushButton("Tare: OFF")
        self.btn_tare_toggle.setCheckable(True)
        self.btn_tare_toggle.setStyleSheet("background-color: #e0e0e0; color: black; font-weight: bold; padding: 10px;")
        self.btn_tare_toggle.clicked.connect(self.toggle_tare_force)

        self.btn_tare_update = QPushButton("Update Zero")
        self.btn_tare_update.setStyleSheet("background-color: #ffc107; color: black; font-weight: bold; padding: 10px;")
        self.btn_tare_update.clicked.connect(self.update_tare_force)
        tare_layout.addWidget(self.btn_tare_toggle)
        tare_layout.addWidget(self.btn_tare_update)
        form_layout.addRow("6-Axis Tare:", tare_layout)

        self.lbl_ch = [QLabel("0.0000 [V]") for _ in range(6)]
        for i in range(6):
            self.lbl_ch[i].setStyleSheet("color: gray;")
            form_layout.addRow(f"CH{i+1}:", self.lbl_ch[i])

        self.lbl_fm = [QLabel("0.0000") for _ in range(6)]
        self.lbl_f_val = QLabel("0.0000")
        
        form_layout.addRow(f"Fx (Drag Dir) [N]:", self.lbl_fm[0])
        form_layout.addRow(f"Fy (Lift Dir) [N]:", self.lbl_fm[1])
        form_layout.addRow(f"Fz [N]:", self.lbl_fm[2])
        form_layout.addRow(f"Mx [N·m]:", self.lbl_fm[3])
        form_layout.addRow(f"My [N·m]:", self.lbl_fm[4])
        form_layout.addRow(f"Mz [N·m]:", self.lbl_fm[5])

        self.lbl_cl = QLabel("0.000")
        self.lbl_cl.setStyleSheet("color: #d90000; font-weight: bold; font-size: 16px;")
        self.lbl_cd = QLabel("0.000")
        self.lbl_cd.setStyleSheet("color: #0000d9; font-weight: bold; font-size: 16px;")
        form_layout.addRow(f"Lift Coeff (CL):", self.lbl_cl)
        form_layout.addRow(f"Drag Coeff (CD):", self.lbl_cd)

        sensor_group.setLayout(form_layout)
        left_layout.addWidget(sensor_group)

        servo_group = QGroupBox("Servo Control")
        servo_layout = QVBoxLayout()
        base_layout = QHBoxLayout()
        base_layout.addWidget(QLabel("Base Angle:"))
        self.spin_base = QSpinBox()
        self.spin_base.setRange(0, 180)
        self.spin_base.setValue(90)
        self.spin_base.valueChanged.connect(self.update_base_angle)
        base_layout.addWidget(self.spin_base)
        servo_layout.addLayout(base_layout)
        mode_layout = QHBoxLayout()
        self.combo_mode = QComboBox()
        self.combo_mode.addItems(["Direct (0-180)", "Feedback (-90 to +90)"])
        mode_layout.addWidget(QLabel("Mode:"))
        mode_layout.addWidget(self.combo_mode)
        servo_layout.addLayout(mode_layout)
        ctrl_layout = QHBoxLayout()
        self.input_angle = QLineEdit()
        self.btn_send = QPushButton("Move")
        self.btn_send.clicked.connect(self.start_servo_control)
        self.btn_center = QPushButton("Center")
        self.btn_center.clicked.connect(self.move_to_center)
        ctrl_layout.addWidget(self.input_angle)
        ctrl_layout.addWidget(self.btn_send)
        ctrl_layout.addWidget(self.btn_center)
        servo_layout.addLayout(ctrl_layout)
        
        self.lbl_status = QLabel("Ready")
        self.lbl_status.setAlignment(Qt.AlignCenter)
        self.lbl_status.setStyleSheet("color: gray; padding: 5px; font-size: 14px;")
        servo_layout.addWidget(self.lbl_status)
        
        servo_group.setLayout(servo_layout)
        left_layout.addWidget(servo_group)
        left_layout.addStretch()

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)

        pg.setConfigOptions(antialias=True)
        
        self.plot_f = pg.PlotWidget(title="Force [N] (Raw Data)")
        self.plot_f.addLegend()
        self.plot_f.showGrid(x=True, y=True)
        self.curve_fx = self.plot_f.plot(pen=pg.mkPen('r', width=1.5), name="Fx")
        self.curve_fy = self.plot_f.plot(pen=pg.mkPen('g', width=1.5), name="Fy")
        self.curve_fz = self.plot_f.plot(pen=pg.mkPen('b', width=1.5), name="Fz")
        right_layout.addWidget(self.plot_f)

        self.plot_m = pg.PlotWidget(title="Moment [N·m] (Raw Data)")
        self.plot_m.addLegend()
        self.plot_m.showGrid(x=True, y=True)
        self.curve_mx = self.plot_m.plot(pen=pg.mkPen('c', width=1.5), name="Mx")
        self.curve_my = self.plot_m.plot(pen=pg.mkPen('m', width=1.5), name="My")
        self.curve_mz = self.plot_m.plot(pen=pg.mkPen('w', width=1.5), name="Mz")
        right_layout.addWidget(self.plot_m)

        self.plot_coeff = pg.PlotWidget(title=f"Aerodynamic Coefficients (Raw Data)")
        self.plot_coeff.addLegend()
        self.plot_coeff.showGrid(x=True, y=True)
        self.curve_cl = self.plot_coeff.plot(pen=pg.mkPen((255, 50, 50), width=2), name="CL")
        self.curve_cd = self.plot_coeff.plot(pen=pg.mkPen((50, 100, 255), width=2), name="CD")
        right_layout.addWidget(self.plot_coeff)

        main_layout.addWidget(left_panel)
        main_layout.addWidget(right_panel, stretch=1)

    def toggle_timer_mode(self, checked):
        if checked:
            self.btn_timer_toggle.setText("Timer: ON")
            self.btn_timer_toggle.setStyleSheet("background-color: #0275d8; color: white; font-weight: bold; padding: 5px;")
            self.spin_duration.setEnabled(True)
        else:
            self.btn_timer_toggle.setText("Timer: OFF")
            self.btn_timer_toggle.setStyleSheet("background-color: #e0e0e0; color: black; font-weight: bold; padding: 5px;")
            self.spin_duration.setEnabled(False)

    def update_ui_elements(self):
        if self.worker and self.worker.is_recording and self.record_start_time:
            elapsed = (datetime.now() - self.record_start_time).total_seconds()
            self.lbl_elapsed.setText(f"{elapsed:.1f} s")

            if self.btn_timer_toggle.isChecked():
                target_duration = self.spin_duration.value()
                if elapsed >= target_duration:
                    self.btn_rec.setChecked(False)
                    self.toggle_recording(False) 

        if len(self.fx_data) == 0:
            return

        if len(self.fx_data) >= UI_MA_WINDOW:
            avg_fx = np.nanmean(list(self.fx_data)[-UI_MA_WINDOW:])
            avg_fy = np.nanmean(list(self.fy_data)[-UI_MA_WINDOW:])
            avg_fz = np.nanmean(list(self.fz_data)[-UI_MA_WINDOW:])
            avg_mx = np.nanmean(list(self.mx_data)[-UI_MA_WINDOW:])
            avg_my = np.nanmean(list(self.my_data)[-UI_MA_WINDOW:])
            avg_mz = np.nanmean(list(self.mz_data)[-UI_MA_WINDOW:])
            avg_cl = np.nanmean(list(self.cl_data)[-UI_MA_WINDOW:])
            avg_cd = np.nanmean(list(self.cd_data)[-UI_MA_WINDOW:])
            avg_wind = np.nanmean(list(self.wind_data)[-UI_MA_WINDOW:])
            avg_pot = np.nanmean(list(self.pot_data)[-UI_MA_WINDOW:])
            
            rho_list = list(self.rho_data)
            avg_rho = np.nanmean(rho_list[-UI_MA_WINDOW:]) if len(rho_list) > 0 else np.nan
            q_list = list(self.q_data)
            avg_q = np.nanmean(q_list[-UI_MA_WINDOW:]) if len(q_list) > 0 else np.nan
            
            avg_sg = [np.nanmean(list(self.sg_data[i])) for i in range(6)]
        else:
            avg_fx, avg_fy, avg_fz = self.fx_data[-1], self.fy_data[-1], self.fz_data[-1]
            avg_mx, avg_my, avg_mz = self.mx_data[-1], self.my_data[-1], self.mz_data[-1]
            avg_cl, avg_cd = self.cl_data[-1], self.cd_data[-1]
            avg_wind, avg_pot = self.wind_data[-1], self.pot_data[-1]
            avg_rho, avg_q = self.rho_data[-1], self.q_data[-1]
            avg_sg = [self.sg_data[i][-1] for i in range(6)]

        self.current_pot_display = avg_pot - self.pot_offset

        latest_temp = self.temp_data[-1] if len(self.temp_data)>0 else np.nan
        latest_hum = self.hum_data[-1] if len(self.hum_data)>0 else np.nan
        latest_pres = self.pres_data[-1] if len(self.pres_data)>0 else np.nan

        self.lbl_temp.setText(f"{latest_temp:.2f} °C") 
        self.lbl_hum.setText(f"{latest_hum:.2f} %")
        self.lbl_pres.setText(f"{latest_pres:.2f} hPa")
        self.lbl_rho.setText(f"{avg_rho:.4f} kg/m³")
        self.lbl_q.setText(f"{avg_q:.2f} Pa")
        self.lbl_wind.setText(f"{avg_wind:.2f} m/s")
        self.lbl_pot.setText(f"{self.current_pot_display:+.1f} deg") 
        
        avg_f_val = np.sqrt(avg_fx**2 + avg_fy**2 + avg_fz**2)

        for i in range(6):
            if abs(avg_sg[i]) >= 3.5:
                self.lbl_ch[i].setStyleSheet("color: red; font-weight: bold;")
            else:
                self.lbl_ch[i].setStyleSheet("color: gray;")
            self.lbl_ch[i].setText(f"{avg_sg[i]:.4f} [V]")
            
        self.lbl_fm[0].setText(f"{avg_fx:.4f}")
        self.lbl_fm[1].setText(f"{avg_fy:.4f}")
        self.lbl_fm[2].setText(f"{avg_fz:.4f}")
        self.lbl_fm[3].setText(f"{avg_mx:.4f}")
        self.lbl_fm[4].setText(f"{avg_my:.4f}")
        self.lbl_fm[5].setText(f"{avg_mz:.4f}")
        self.lbl_f_val.setText(f"{avg_f_val:.4f}")
            
        self.lbl_cl.setText(f"{avg_cl:.3f}")
        self.lbl_cd.setText(f"{avg_cd:.3f}")

        x_list = list(self.x_data)
        self.curve_fx.setData(x_list, list(self.fx_data))
        self.curve_fy.setData(x_list, list(self.fy_data))
        self.curve_fz.setData(x_list, list(self.fz_data))
        
        self.curve_mx.setData(x_list, list(self.mx_data))
        self.curve_my.setData(x_list, list(self.my_data))
        self.curve_mz.setData(x_list, list(self.mz_data))

        self.curve_cl.setData(x_list, list(self.cl_data))
        self.curve_cd.setData(x_list, list(self.cd_data))

    def toggle_recording(self, checked):
        if self.worker:
            if checked:
                memo = self.input_memo.text()
                duration_str = f"{self.spin_duration.value()} s" if self.btn_timer_toggle.isChecked() else "Manual"
                
                # 直近の平均風速を計算
                avg_wind = 0.0
                if len(self.wind_data) > 0:
                    avg_wind_calc = np.nanmean(list(self.wind_data)[-UI_MA_WINDOW:])
                    if not np.isnan(avg_wind_calc):
                        avg_wind = float(avg_wind_calc)
                
                self.worker.set_recording(True, memo, str(SAIL_AREA), duration_str, avg_wind)
                self.record_start_time = datetime.now()
                self.lbl_rec_status.setText("● REC")
                self.lbl_rec_status.setStyleSheet("color: red; font-weight: bold; font-size: 14px;")
                self.btn_rec.setText("Stop REC (ON)")
                self.btn_rec.setStyleSheet("background-color: #d9534f; color: white; font-weight: bold; padding: 10px;")
            else:
                self.worker.set_recording(False)
                self.record_start_time = None
                self.lbl_rec_status.setText("Standby")
                self.lbl_rec_status.setStyleSheet("color: gray; font-weight: bold; font-size: 14px;")
                self.lbl_elapsed.setText("0.0 s")
                self.btn_rec.setText("Start REC (OFF)")
                self.btn_rec.setStyleSheet("background-color: #e0e0e0; color: black; font-weight: bold; padding: 10px;")

    def toggle_tare_force(self, checked):
        if checked:
            self.btn_tare_toggle.setText("Tare: ON")
            self.btn_tare_toggle.setStyleSheet("background-color: #0275d8; color: white; font-weight: bold; padding: 10px;")
        else:
            self.btn_tare_toggle.setText("Tare: OFF")
            self.btn_tare_toggle.setStyleSheet("background-color: #e0e0e0; color: black; font-weight: bold; padding: 10px;")

    def update_tare_force(self):
        self.fm_offset = np.copy(self.latest_fm_raw)
        if not self.btn_tare_toggle.isChecked():
            self.btn_tare_toggle.setChecked(True)
            self.toggle_tare_force(True)

    def update_base_angle(self, val):
        self.servo_center_val = float(val)
        self.last_servo_command = float(val)

    def refresh_ports(self):
        self.port_combo.clear()
        for p in serial.tools.list_ports.comports():
            self.port_combo.addItem(p.device)

    def toggle_connection(self):
        if self.worker is None:
            port = self.port_combo.currentText()
            if not port: return
            self.worker = SerialWorker(port)
            self.worker.data_received.connect(self.receive_serial_data)
            self.worker.connection_success.connect(self.on_worker_connected)
            self.worker.start()
            self.btn_connect.setText("Disconnect")
            self.btn_rec.setEnabled(True)
        else:
            self.feedback_timer.stop()
            self.worker.stop()
            self.worker = None
            self.btn_connect.setText("Connect")
            self.btn_rec.setChecked(False)
            self.btn_rec.setEnabled(False)
            self.lbl_rec_status.setText("Disconnected")
            self.lbl_status.setText("Disconnected")
            self.lbl_status.setStyleSheet("color: gray; padding: 5px; font-size: 14px;")

    @Slot()
    def on_worker_connected(self):
        self.send_servo_raw(self.servo_center_val)
        self.btn_zero_pot.setEnabled(True)
        self.lbl_status.setText("Connected / Ready")

    def set_zero_pot(self):
        if len(self.pot_data) > 0:
            self.pot_offset = np.nanmean(list(self.pot_data)[-UI_MA_WINDOW:])
            self.lbl_pot.setText(f"+0.0 deg (Set)")

    @Slot(float, float, float, float, float, object, object, float)
    def receive_serial_data(self, temp, hum, pres, pot, wind, sg_array, fm_array, f_val):
        if not np.isnan(temp): self.temp_data.append(temp)
        if not np.isnan(hum): self.hum_data.append(hum)
        if not np.isnan(pres): self.pres_data.append(pres)
        if not np.isnan(wind): self.wind_data.append(wind)
        if not np.isnan(pot): self.pot_data.append(pot)

        avg_temp = np.mean(self.temp_data) if len(self.temp_data) > 0 else np.nan
        avg_hum = np.mean(self.hum_data) if len(self.hum_data) > 0 else np.nan
        avg_pres = np.mean(self.pres_data) if len(self.pres_data) > 0 else np.nan

        if not np.isnan(avg_temp) and not np.isnan(avg_hum) and not np.isnan(avg_pres):
            Tk = avg_temp + 273.15
            if Tk > 0:
                Es = 6.1078 * 10.0 ** ((7.5 * avg_temp) / (avg_temp + 237.3))
                Pv = Es * (avg_hum / 100.0)
                Pd = avg_pres - Pv
                rho = (Pd * 100.0 / (287.05 * Tk)) + (Pv * 100.0 / (461.495 * Tk))
            else:
                rho = np.nan
        else:
            rho = np.nan
            
        if not np.isnan(rho): self.rho_data.append(rho)

        q = 0.5 * rho * (wind ** 2) if not np.isnan(rho) and not np.isnan(wind) else np.nan
        if not np.isnan(q): self.q_data.append(q)

        self.latest_fm_raw = fm_array
        if self.btn_tare_toggle.isChecked():
            display_fm = fm_array - self.fm_offset
        else:
            display_fm = fm_array

        if not np.isnan(q) and q > 0.5: 
            cl = display_fm[1] / (q * SAIL_AREA)
            cd = -display_fm[0] / (q * SAIL_AREA)
        else:
            cl = 0.0
            cd = 0.0

        for i in range(6):
            self.sg_data[i].append(sg_array[i])
            
        self.time_idx += 1
        self.x_data.append(self.time_idx)
        self.fx_data.append(display_fm[0])
        self.fy_data.append(display_fm[1])
        self.fz_data.append(display_fm[2])
        self.mx_data.append(display_fm[3])
        self.my_data.append(display_fm[4])
        self.mz_data.append(display_fm[5])
        self.cl_data.append(cl)
        self.cd_data.append(cd)

    def send_servo_raw(self, angle_val):
        if self.worker:
            clamped_val = max(0.0, min(180.0, float(angle_val)))
            self.worker.send_servo_angle(clamped_val)
            self.last_servo_command = clamped_val

    def move_to_center(self):
        self.send_servo_raw(self.servo_center_val)
        self.lbl_status.setText(f"Moved to Center: {self.servo_center_val:.1f} deg")
        self.lbl_status.setStyleSheet("color: black; padding: 5px; font-size: 14px;")

    def start_servo_control(self):
        if self.worker is None: return
        self.feedback_timer.stop()
        self.lbl_status.setStyleSheet("color: black; padding: 5px; font-size: 14px;")
        
        try:
            val = float(self.input_angle.text())
            if self.combo_mode.currentIndex() == 0:
                self.send_servo_raw(val)
                self.lbl_status.setText(f"Sent Direct Command: {val:.1f} deg")
            else:
                # ★ 変数の初期化と大移動の開始
                self.feedback_target = val
                self.feedback_iteration = 0
                self.rough_move_timeout = 0
                self.rough_stop_count = 0
                self.prev_pot_display = None
                self.feedback_state = 'ROUGH_MOVING'
                
                self.send_servo_raw(self.servo_center_val + val)
                self.lbl_status.setText(f"Target: {val:.1f} deg. Rough moving...")
                self.lbl_status.setStyleSheet("color: blue; font-weight: bold; padding: 5px; font-size: 14px;")
                
                # 大移動中は監視のため0.5秒間隔で高速チェック
                self.feedback_timer.start(500) 
        except ValueError:
            self.lbl_status.setText("Error: Invalid Number")
            self.lbl_status.setStyleSheet("color: red; font-weight: bold; padding: 5px; font-size: 14px;")

    # ★ 3段階フィードバック制御処理
    def process_feedback_loop(self):
        error = self.feedback_target - self.current_pot_display

        # --- 状態1: 大移動 (ROUGH_MOVING) ---
        if self.feedback_state == 'ROUGH_MOVING':
            self.rough_move_timeout += 1
            
            # 回転が停止したか（0.5秒での変化量が0.2度未満か）を監視
            if self.prev_pot_display is not None:
                delta = abs(self.current_pot_display - self.prev_pot_display)
                if delta < 0.2:
                    self.rough_stop_count += 1
                else:
                    self.rough_stop_count = 0
            self.prev_pot_display = self.current_pot_display

            # 誤差が2度以内、または「1秒間(2カウント)動きが完全に止まった」ら微調整へ強制移行
            if abs(error) <= 2.0 or self.rough_stop_count >= 2:
                self.feedback_state = 'ADJUSTING'
                self.feedback_iteration = 0
                self.feedback_timer.setInterval(FEEDBACK_INTERVAL) # 微調整は3秒間隔
                self.lbl_status.setText(f"Rough move done. Starting fine adjustment (Diff: {error:.2f} deg)")
                
                # すぐに1回目の微調整コマンドを発行して待機時間を短縮
                new_command = self.last_servo_command + (error * FEEDBACK_DAMPING)
                self.send_servo_raw(new_command)
                return

            if self.rough_move_timeout > 120: # 60秒でタイムアウト
                self.finish_feedback(f"Rough move timeout! (Diff: {error:.2f} deg)", success=False)
            else:
                self.lbl_status.setText(f"Rough moving... (Diff: {error:.2f} deg)")
            return

        # --- 状態3: 待機後の最終確認 (WAITING) ---
        elif self.feedback_state == 'WAITING':
            if abs(error) <= FEEDBACK_TOLERANCE_FINAL:
                self.finish_feedback(f"Target Reached! [ OK ] (Diff: {error:.2f} deg)", success=True)
                return
            else:
                self.feedback_state = 'ADJUSTING'
                self.feedback_timer.setInterval(FEEDBACK_INTERVAL)
                self.lbl_status.setText(f"Verification failed. Readjusting... (Diff: {error:.2f} deg)")
                
                # ここでもすぐに微調整コマンドを発行
                new_command = self.last_servo_command + (error * FEEDBACK_DAMPING)
                self.send_servo_raw(new_command)
                return

        # --- 状態2: 微調整 (ADJUSTING) ---
        if self.feedback_state == 'ADJUSTING':
            self.feedback_iteration += 1
            
            if abs(error) <= FEEDBACK_TOLERANCE_STRICT:
                self.feedback_state = 'WAITING'
                self.lbl_status.setText(f"Almost there... Waiting 1s to verify (Diff: {error:.2f} deg)")
                self.feedback_timer.setInterval(1000) # 1秒後に最終確認
                return

            if self.feedback_iteration >= MAX_ITERATIONS:
                self.finish_feedback(f"Timeout. (Diff: {error:.2f} deg)", success=False)
                return

            new_command = self.last_servo_command + (error * FEEDBACK_DAMPING)
            self.send_servo_raw(new_command)
            self.lbl_status.setText(f"Adjusting... Iter:{self.feedback_iteration} Error:{error:.2f} deg")

    def finish_feedback(self, msg, success=False):
        self.feedback_timer.stop()
        self.lbl_status.setText(msg)
        if success:
            self.lbl_status.setStyleSheet("background-color: #28a745; color: white; font-weight: bold; padding: 5px; border-radius: 5px; font-size: 16px;")
        else:
            self.lbl_status.setStyleSheet("background-color: #dc3545; color: white; font-weight: bold; padding: 5px; border-radius: 5px; font-size: 16px;")

    def closeEvent(self, event):
        if self.worker:
            self.worker.stop()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
import os
import cv2
import threading
import time
import json
import atexit
import secrets
import datetime
import numpy as np
import sounddevice as sd
from loguru import logger
from flask import Flask, render_template, Response, request, send_from_directory
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage
from linebot.models import ButtonsTemplate, ConfirmTemplate, MessageAction, TemplateSendMessage
secret_data = json.load(open('secret.json'))
# ---------------------可調整區域----------------------
I2C_OLED = True            # 是否啟用OLED，未開啟會在終端機出現
gpio_enable = True         # 啟用GPIO
base_url = 'https://private-resource.example.com'
line_bot_api = LineBotApi(secret_data['CHANNEL_ACCESS_TOKEN']) # YOUR_CHANNEL_ACCESS_TOKEN
handler = WebhookHandler(secret_data['CHANNEL_SECRET']) # YOUR_CHANNEL_SECRET
userID = secret_data['USER_ID'] # 使用者ID
# ------------------GPIO 初始化------------------------
if gpio_enable:
    import RPi.GPIO as GPIO
    GPIO.setmode(GPIO.BCM)   # GPIO模式
    # 伺服馬達部分
    motorPin = 18 # 腳位 18
    GPIO.setup(motorPin, GPIO.OUT) # 輸出腳位OUT
    # 蜂鳴器部分
    BeepPin = 17 # 腳位 17
    GPIO.setup(BeepPin, GPIO.OUT) # 輸出腳位OUT
    # 門鈴部分
    button_pin = 25
    GPIO.setup(button_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
# ----------GPIO伺服馬達輸出設定區域 (樹梅派)------------
def operate_motor(status: str, freq: int = 50) -> None:
    """
    控制伺服馬達開關，可調整裡面的degrees來決定動作。
    :param status: str，馬達狀態，可選值為 "open" 或 "close"。
    :param freq: int，伺服馬達頻率控制，預設50。
    """
    if gpio_enable:        
        SG90 = GPIO.PWM(motorPin, freq)  # 建立實體物件, 腳位, 頻率
        SG90.start(0)            # 開始

        def duty_cycle_angle(angle=0):
            duty_cycle = (0.05 * freq) + (0.19 * freq * angle / 180)
            return duty_cycle

        def move(degree, wait_time):
            duty_cycle = duty_cycle_angle(degree)
            # print(f"{degree}度 = {duty_cycle}週期")
            SG90.ChangeDutyCycle(duty_cycle)
            time.sleep(wait_time)

        if status == "open":
            degrees = [150]  # 150開 50關
        elif status == "close":
            degrees = [50]  # 50關
        else:
            raise ValueError("Invalid status value. Must be either 'open' or 'close'.")

        for degree in degrees:
            move(degree, wait_time=0.35)

        SG90.stop()
    else:
        if status == "open":
            print("門打開")
        elif status == "close":
            print("門關閉")
        else:
            raise ValueError("Invalid status value. Must be either 'open' or 'close'.")
# ------- I2C OLED 點矩陣液晶顯示器(樹梅派)(僅供參考)---
if I2C_OLED:
    from luma.core.interface.serial import i2c, spi
    from luma.core.render import canvas
    from luma.oled.device import ssd1306, ssd1325, ssd1331, sh1106
    serial = i2c(port=1, address=0x3C)
    device = ssd1306(serial)
# -------------------- OLED控制 ----------------------
oled_cache = [] # OLED 的暫存
def oled_control(text_list):
    """
    更新OLED顯示，使用新的字串list。
    :param text_list: list，包含2行要在OLED上顯示的字串。 [第一行, 第二行]
    """
    global oled_cache
    if oled_cache != text_list:
        oled_cache = text_list
        try:
            if I2C_OLED:
                device.cleanup = ""
                with canvas(device) as draw:
                    draw.rectangle(device.bounding_box, outline="white", fill="black")
                    if len(text_list[0]) < 15:
                        draw.text((30, 20), F"{text_list[0]}\n{text_list[1]}", fill="white")
                    else:
                        draw.text((20, 20), F"{text_list[0]}\n{text_list[1]}", fill="white")
            else:
                for index in range(2):
                    logger.debug(F"OLED 正在顯示: {text_list[index]}")
        except Exception as e:
            logger.error(F"OLED顯示發生錯誤: {e}")
# --------------------------------------------------------
app = Flask(__name__)
frame_lock = threading.Lock()  # 用於控制串流生成函數的同步
latest_image_filename = None
image_folder = os.path.join(app.root_path, 'image')
TOKEN_VALID_DURATION = 30 * 60  # 30 分鐘，單位為秒
tokens = {}  # 儲存 token 和 產生的時間

def generate_token():
    token = secrets.token_hex(10)
    tokens[token] = time.time()  # 儲存 token 及其生成時間
    return token

def validate_token(token):
    if token in tokens:
        generated_time = tokens[token]
        current_time = time.time()
        if current_time - generated_time <= TOKEN_VALID_DURATION:
            del tokens[token]  # 使用後自動失效，從字典中移除 token
            return True
        else:
            del tokens[token]  # 移除已過期的 token
    return False

def is_preview_agent(user_agent):
    # 檢查 User-Agent 是否為預覽機制使用的標頭
    preview_agents = ['facebookexternalhit', 'Googlebot', 'Twitterbot']
    for agent in preview_agents:
        if agent in user_agent:
            return True
    return False

def generate_frames():
    camera = cv2.VideoCapture(0)  # 使用預設攝影機
    while True:
        success, frame = camera.read()
        if not success:
            break
        else:
            ret, buffer = cv2.imencode('.jpg', frame)
            frame = buffer.tobytes()
            with frame_lock:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
    camera.release()

    
def streaming():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/webcam')
def webcam():
    token = request.args.get('token')
    user_agent = request.headers.get('User-Agent')
    if not is_preview_agent(user_agent):
        if validate_token(token):
            # 驗證通過，啟動攝影機串流
            return streaming()
        else:
            return render_template("401.html"), 401  # 返回自定義的 401 錯誤頁面
    elif is_preview_agent(user_agent):
        return "Access Denied: Preview agents are not allowed", 403
# ----------------LINE Bot 連接部分-------------------------------
@app.route('/line_webhook', methods=['POST'])
def line_webhook():
    # 獲取 LINE Bot 的請求頭部資訊
    signature = request.headers['X-Line-Signature']
    
    # 解析請求內容
    body = request.get_data(as_text=True)
    
    try:
        # 驗證請求的簽章
        handler.handle(body, signature)
    except InvalidSignatureError:
        # 簽章驗證失敗，回傳 400 錯誤
        return 'Invalid signature', 400
    logger.info("LINE Webhook received")
    return 'OK', 200
# --------------圖片抓取區域-----------------------------------------
@app.route('/image/<filename>')
def get_image(filename):
    # 指定圖片資料夾的路徑
    image_folder = os.path.join(app.root_path, 'image')
    
    # 檢查圖片是否存在
    image_path = os.path.join(image_folder, filename)
    if os.path.isfile(image_path):
        # 傳送圖片給客戶端
        return send_from_directory(image_folder, filename)
    # 若圖片不存在，回傳 404 錯誤
    return 'Image not found', 404

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    # user_id = event.source.user_id
    # print(f"使用者 ID：{user_id}")

    message = event.message.text

    if message == '!DOOR_ACTION':
        # 建立 ConfirmTemplate 物件，設定按鈕樣式和兩個按鈕選項
        confirm_template = ConfirmTemplate(
            text='門鎖控制選單',
            actions=[
                MessageAction(label='開門', text='!DOOR_OPEN'),
                MessageAction(label='關門', text='!DOOR_CLOSE')
            ]
        )
        
        # 建立 TemplateSendMessage 物件，設定 ConfirmTemplate 為內容
        template_message = TemplateSendMessage(alt_text='門鎖動作', template=confirm_template)
        
        # 回傳 Template message
        line_bot_api.reply_message(event.reply_token, template_message)
    elif message == '!DOOR_OPEN':
        operate_motor("open")
        oled_control(['Door Status:', "Open"])
        logger.debug("門打開了")
    elif message == '!DOOR_CLOSE':
        operate_motor("close")
        oled_control(['Door Status:', "Close"])
        logger.debug("門關閉了")
    elif message == '!TEST':
        push_doorbell_notification()
        logger.debug("測試訊息發送")

def capture_image():
    global latest_image_filename

    camera = cv2.VideoCapture(0)  # 使用預設攝影機
    success, frame = camera.read()
    if success:
        # 建立檔名：日期時間.jpg
        now = datetime.datetime.now()
        filename = now.strftime('%Y%m%d%H%M%S') + '.jpg'
        # 檔案完整路徑
        file_path = os.path.join(image_folder, filename)

        # 儲存圖片
        cv2.imwrite(file_path, frame)

        # 更新最新圖片檔名
        latest_image_filename = filename

    camera.release()

def push_doorbell_notification():
    logger.info("門鈴被按下了")
    global latest_image_filename  # 使用 global 關鍵字聲明全域變數
    token = generate_token()
    # 拍照並儲存圖片
    capture_image()

    # 如果有最新的圖片檔名，將圖片 URL 加入通知訊息中
    image_url = None
    if latest_image_filename:
        image_url = f'{base_url}/image/{latest_image_filename}'
        
    buttons_template = ButtonsTemplate(
        title='偵測到有人按下門鈴！',
        text='請選擇以下動作：',
        thumbnail_image_url=image_url,  # 縮圖圖片的 URL
        actions=[
            MessageAction(label='查看攝影機頁面', text=f'{base_url}/webcam?token={token}')
        ]
    )

    template_message = TemplateSendMessage(
        alt_text='接收到門鈴',
        template=buttons_template
    )

    line_bot_api.push_message(userID, template_message)

    # 清除最新圖片檔名
    latest_image_filename = None
    
if gpio_enable:
    # piano = [262,294,330,349,392,440,494,524,588,660,698,784,880,988,1048,1176,1320,1396,1568,1760,1976]
    def play(pitch, sec):
        half_pitch = (1 / pitch) / 2
        t = int(pitch * sec)
        for i in range(t):
            GPIO.output(17, GPIO.HIGH)
            time.sleep(half_pitch)
            GPIO.output(17, GPIO.LOW)
            time.sleep(half_pitch)
    def doorbell_callback(channel):
        logger.info("門鈴按鈕被按下！")
        # for p in piano:
        play(988, 1)
        push_doorbell_notification()
    GPIO.add_event_detect(button_pin, GPIO.FALLING, callback=doorbell_callback, bouncetime=1000)

def set_logger(): # log系統
    log_format = (
        '{time:YYYY-MM-DD HH:mm:ss} | '
        '{level} | <{module}>:{function}:{line} | '
        '{message}'
    )
    logger.add(
        './logs/system.log',
        rotation='7 day',
        retention='30 days',
        level='INFO',
        encoding='UTF-8',
        format=log_format
    )

def cleanup():
    GPIO.cleanup()

if __name__ == '__main__':
    if gpio_enable:
        atexit.register(cleanup)
    set_logger()
    operate_motor("close")
    oled_control(['Door Status:', "Close"])
    app.run(host='0.0.0.0',port=8080) # 啟動Flask
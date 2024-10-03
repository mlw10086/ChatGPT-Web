import aiohttp
import asyncio
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from threading import Thread, Event, Lock
from queue import Queue
import mimetypes
import pyperclip
from tkinterdnd2 import DND_FILES, TkinterDnD
import time
import aiofiles
import json
import sys
import webbrowser
import atexit
import signal
import logging
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from PIL import Image, ImageTk, ImageFilter

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

CURRENT_VERSION = "2.0.0"

class UploadManager:
    def __init__(self):
        self.stop_event = Event()
        self.pause_event = Event()
        self.uploaded = 0
        self.total_size = 0
        self.lock = Lock()
        self.upload_completed = False

    def stop(self):
        self.stop_event.set()

    def pause(self):
        self.pause_event.set()

    def resume(self):
        self.pause_event.clear()

    def update_progress(self, chunk_size):
        with self.lock:
            self.uploaded += chunk_size

    def complete_upload(self):
        with self.lock:
            self.upload_completed = True
            self.uploaded = self.total_size

async def get_upload_url(session, url='https://www.cngov.email/video/proxy.php'):
    try:
        async with session.get(url, ssl=False) as response:
            response.raise_for_status()
            return await response.text()
    except aiohttp.ClientError as e:
        return f"获取上传地址失败: {e}"

async def upload_file_async(file_path, progress_queue, message_queue, upload_manager):
    try:
        file_path = file_path.strip('{}')  # 移除可能存在的花括号
        file_path = os.path.normpath(file_path)
        if not os.path.exists(file_path):
            message_queue.put(f"错误: 文件不存在: {file_path}")
            return None

        file_size = os.path.getsize(file_path)
        upload_manager.total_size = file_size
        message_queue.put(f"开始上传文件，总大小: {file_size} 字节")

        file_name = os.path.basename(file_path)
        content_type, _ = mimetypes.guess_type(file_path)
        if not content_type:
            content_type = 'application/octet-stream'

        headers = {
            'Content-Type': content_type,
            'Content-Disposition': f'inline; filename="{file_name}"',
        }

        async def file_sender(file_name):
            async with aiofiles.open(file_name, 'rb') as f:
                chunk = await f.read(8192)
                while chunk:
                    yield chunk
                    upload_manager.update_progress(len(chunk))
                    chunk = await f.read(8192)

        timeout = aiohttp.ClientTimeout(total=3600)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            upload_url = await get_upload_url(session)

            try:
                async with session.put(upload_url, data=file_sender(file_path), headers=headers, ssl=False) as response:
                    response.raise_for_status()
            except aiohttp.ClientError as e:
                message_queue.put(f"上传失败，错误: {str(e)}")
                return None
            except asyncio.TimeoutError:
                message_queue.put("上传超时")
                return None

        upload_manager.complete_upload()
        message_queue.put(f"上传成功: {upload_url}")
        message_queue.put(f"文件上传完成，总共上传 {file_size} 字节")
        await record_upload(upload_url, file_path, file_size)

        return upload_url
    except Exception as e:
        message_queue.put(f"上传错误: {str(e)}")
        return None

async def record_upload(upload_url, file_path, file_size):
    data = {
        'url': upload_url,
        'filename': os.path.basename(file_path),
        'timestamp': int(time.time()),
        'file_size': file_size  # 添加文件大小
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post('https://www.cngov.email/admin/record_upload.php', json=data) as response:
                await response.text()  
        except Exception:
            pass  

async def check_for_updates(max_retries=3):
    urls = [
        'https://www.cngov.email/admin/check_update.php',
        'http://www.cngov.email/admin/check_update.php'  # 备用 HTTP 地址
    ]
    for attempt in range(max_retries):
        for url in urls:
            try:
                logging.debug(f"正在检查更新，当前版本：{CURRENT_VERSION}")
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=10) as response:
                        if response.status == 200:
                            data = await response.json()
                            logging.debug(f"从 {url} 收到更新信息：{data}")
                            return data
                        else:
                            logging.error(f"从 {url} 检查更新失败，状态码：{response.status}")
            except Exception as e:
                logging.error(f"从 {url} 检查更新时发生错误（尝试 {attempt+1}/{max_retries}）：{str(e)}")
        
        if attempt < max_retries - 1:
            await asyncio.sleep(2)  # 等待2秒后重试
    
    return None

async def test_network_connection():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get('https://www.baidu.com') as response:
                if response.status == 200:
                    logging.info("网络连接正常")
                    return True
                else:
                    logging.error(f"网络连接测试失败，状态码：{response.status}")
    except Exception as e:
        logging.error(f"网络连接测试时发生错误：{str(e)}")
    return False

def update_software():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    if not loop.run_until_complete(test_network_connection()):
        messagebox.showerror("网络错误", "无法连接到网络，请检查您的网络设置。")
        return

    update_info = loop.run_until_complete(check_for_updates())
    
    if update_info and 'version' in update_info and 'download_url' in update_info:
        logging.info(f"服务器版本：{update_info['version']}")
        if update_info['version'] > CURRENT_VERSION:
            result = messagebox.askyesno("软件更新", f"发现新版本 {update_info['version']}，是否立即更新？")
            if result:
                webbrowser.open(update_info['download_url'])
                sys.exit()
            else:
                messagebox.showinfo("强制更新", "软件需要更新才能继续使用。")
                sys.exit()
    else:
        logging.warning("未能获取有效的更新信息")

def upload_file(file_path, progress_queue, message_queue, upload_manager):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop.run_until_complete(upload_file_async(file_path, progress_queue, message_queue, upload_manager))

def update_progress(progress_queue, message_queue, upload_manager):
    last_progress = -1
    while not upload_manager.stop_event.is_set() and not upload_manager.upload_completed:
        with upload_manager.lock:
            if upload_manager.total_size > 0:
                progress = int((upload_manager.uploaded / upload_manager.total_size) * 100)
                if progress != last_progress:
                    progress_queue.put(progress)
                    message_queue.put(f"已上传: {upload_manager.uploaded}/{upload_manager.total_size} 字节 ({progress}%)")
                    last_progress = progress
        time.sleep(0.5)

async def get_announcement():
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get('https://www.cngov.email/admin/get_announcement.php') as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('announcement', '')
        except Exception:
            return ''

def show_announcement():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    announcement = loop.run_until_complete(get_announcement())
    
    if announcement:
        messagebox.showinfo("公告", announcement)
    else:
        messagebox.showinfo("公告", "暂无公告")

async def update_user_status(action='ping'):
    async with aiohttp.ClientSession() as session:
        try:
            url = f'https://www.cngov.email/admin/user_status.php?action={action}'
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('online_users', 0)
        except Exception:
            return 0

def login_user():
    asyncio.run(update_user_status('login'))

def logout_user():
    asyncio.run(update_user_status('logout'))

def update_online_users(root, online_users_label):
    async def update():
        online_users = await update_user_status()
        online_users_label.config(text=f"在线用户: {online_users}")
        root.after(30000, lambda: update_online_users(root, online_users_label)) 

    asyncio.run(update())

def signal_handler(sig, frame):
    logout_user()
    sys.exit(0)

class IKUNStyle(ttk.Style):
    def __init__(self):
        super().__init__()
        self.theme_create("IKUN", parent="cosmo", settings={
            "TFrame": {"configure": {"background": "#ffffff80"}},
            "TLabel": {"configure": {"background": "#ffffff80"}},
            "TButton": {"configure": {"background": "#ffffff80"}},
            "TEntry": {"configure": {"fieldbackground": "#ffffff80"}},
            "Vertical.TScrollbar": {"configure": {"background": "#ffffff80", "troughcolor": "#ffffff40"}},
            "Horizontal.TProgressbar": {"configure": {"background": "#ff69b480", "troughcolor": "#ffffff40"}},
        })

def create_blur_background(image_path, blur_radius=15):
    try:
        image = Image.open(image_path)
        blurred_image = image.filter(ImageFilter.GaussianBlur(blur_radius))
        return ImageTk.PhotoImage(blurred_image)
    except FileNotFoundError:
        print(f"背景图片未找到: {image_path}")
        return None

def create_gui():
    root = TkinterDnD.Tk()
    style = ttk.Style(theme="flatly")
    root.title("文件上传器")
    root.geometry("600x550")

    update_software()

    file_path = tk.StringVar()
    progress_var = tk.IntVar()
    progress_queue = Queue()
    message_queue = Queue()
    upload_manager = UploadManager()

    # 创建IKUN主题
    ikun_style = IKUNStyle()

    def select_file():
        path = filedialog.askopenfilename()
        if path:
            file_path.set(path.strip('{}'))  # 移除可能存在的花括号

    def start_upload():
        path = file_path.get()
        if not path:
            messagebox.showerror("错误", "请先选择文件")
            return
        progress_var.set(0)
        status_label.config(text="准备上传...")
        log_text.delete(1.0, tk.END)
        nonlocal upload_manager
        upload_manager = UploadManager()
        Thread(target=upload_file, args=(path, progress_queue, message_queue, upload_manager)).start()
        Thread(target=update_progress, args=(progress_queue, message_queue, upload_manager)).start()
        root.after(100, check_queue)
        start_button.config(state=tk.DISABLED)
        pause_button.config(state=tk.NORMAL)
        stop_button.config(state=tk.NORMAL)

    def pause_resume_upload():
        if upload_manager.pause_event.is_set():
            upload_manager.resume()
            pause_button.config(text="暂停")
            status_label.config(text="继续上传...")
        else:
            upload_manager.pause()
            pause_button.config(text="继续")
            status_label.config(text="已暂停")

    def stop_upload():
        upload_manager.stop()
        start_button.config(state=tk.NORMAL)
        pause_button.config(state=tk.DISABLED)
        stop_button.config(state=tk.DISABLED)
        status_label.config(text="已停止")

    def clear_info():
        file_path.set("")
        progress_var.set(0)
        status_label.config(text="")
        log_text.delete(1.0, tk.END)
        result_entry.delete(0, tk.END)

    def copy_result():
        result = result_entry.get()
        if result:
            pyperclip.copy(result)
            messagebox.showinfo("复制成功", "上传结果已复制到剪贴板")

    def check_queue():
        try:
            while True:
                progress = progress_queue.get_nowait()
                progress_var.set(progress)
        except:
            pass

        try:
            while True:
                message = message_queue.get_nowait()
                log_text.insert(tk.END, message + "\n")
                log_text.see(tk.END)
                status_label.config(text=message)
                if "上传成功" in message:
                    start_button.config(state=tk.NORMAL)
                    pause_button.config(state=tk.DISABLED)
                    stop_button.config(state=tk.DISABLED)
                    result_entry.delete(0, tk.END)
                    result_entry.insert(0, message.split(": ")[1])
        except:
            pass

        root.after(100, check_queue)

    def on_drop(event):
        path = event.data.strip('{}')  # 移除可能存在的花括号
        if os.path.exists(path):
            file_path.set(path)
        else:
            messagebox.showerror("错误", f"文件不存在: {path}")

    def change_theme():
        new_theme = theme_var.get()
        if new_theme == "IKUN":
            style.theme_use("IKUN")
            # 设置毛玻璃背景
            bg_image_path = "https://jumpy-prod-data-1302954538.cos.accelerate.myqcloud.com/adam2eve/stable/faceSwap/20240920/1726841344000.jpeg"  # 请替换为实际的图片路径
            blur_bg = create_blur_background(bg_image_path)
            if blur_bg:
                bg_label = tk.Label(root, image=blur_bg)
                bg_label.image = blur_bg  # 保持引用
                bg_label.place(x=0, y=0, relwidth=1, relheight=1)
            main_frame.configure(style='TFrame')
        else:
            style.theme_use(new_theme)
            # 移除毛玻璃背景
            for widget in root.winfo_children():
                if isinstance(widget, tk.Label) and widget.winfo_class() == "Label":
                    widget.destroy()
        
        # 更新所有小部件的样式
        update_widget_styles(main_frame)

    def update_widget_styles(widget):
        if isinstance(widget, (ttk.Frame, ttk.Label, ttk.Button, ttk.Entry, ttk.Scrollbar, ttk.Progressbar)):
            widget.configure(style=widget.winfo_class())
        for child in widget.winfo_children():
            update_widget_styles(child)

    main_frame = ttk.Frame(root, padding="20 20 20 0")
    main_frame.pack(fill=tk.BOTH, expand=True)

    # 添加查看公告按钮
    announcement_button = ttk.Button(main_frame, text="查看公告", command=show_announcement, style="info.TButton")
    announcement_button.pack(fill=tk.X, pady=(0, 10))

    file_frame = ttk.Frame(main_frame)
    file_frame.pack(fill=tk.X, pady=(0, 10))

    file_entry = ttk.Entry(file_frame, textvariable=file_path)
    file_entry.pack(side=tk.LEFT, expand=True, fill=tk.X)
    file_entry.drop_target_register(DND_FILES)
    file_entry.dnd_bind('<<Drop>>', on_drop)
    file_entry.bind('<Control-v>', lambda e: file_path.set(root.clipboard_get().strip('{}')))

    select_button = ttk.Button(file_frame, text="选择文件", command=select_file, style="Outline.TButton")
    select_button.pack(side=tk.RIGHT, padx=(5, 0))

    progressbar = ttk.Progressbar(main_frame, variable=progress_var, maximum=100, style="success.Horizontal.TProgressbar")
    progressbar.pack(fill=tk.X, pady=(0, 10))

    status_label = ttk.Label(main_frame, text="", style="info.TLabel")
    status_label.pack(fill=tk.X, pady=(0, 5))

    button_frame = ttk.Frame(main_frame)
    button_frame.pack(fill=tk.X, pady=(0, 10))

    start_button = ttk.Button(button_frame, text="上传", command=start_upload, style="success.TButton")
    start_button.pack(side=tk.LEFT, padx=(0, 5))

    pause_button = ttk.Button(button_frame, text="暂停", command=pause_resume_upload, state=tk.DISABLED, style="warning.TButton")
    pause_button.pack(side=tk.LEFT, padx=5)

    stop_button = ttk.Button(button_frame, text="停止", command=stop_upload, state=tk.DISABLED, style="danger.TButton")
    stop_button.pack(side=tk.LEFT, padx=5)

    clear_button = ttk.Button(button_frame, text="清除信息", command=clear_info, style="info.Outline.TButton")
    clear_button.pack(side=tk.LEFT, padx=5)

    result_frame = ttk.Frame(main_frame)
    result_frame.pack(fill=tk.X, pady=(0, 10))

    result_entry = ttk.Entry(result_frame)
    result_entry.pack(side=tk.LEFT, expand=True, fill=tk.X)

    copy_button = ttk.Button(result_frame, text="复制", command=copy_result, style="info.TButton")
    copy_button.pack(side=tk.RIGHT, padx=(5, 0))

    log_text = scrolledtext.ScrolledText(main_frame, height=15)
    log_text.pack(fill=tk.BOTH, expand=True)

    # 主题选择
    theme_frame = ttk.Frame(main_frame)
    theme_frame.pack(fill=tk.X, pady=(10, 0))

    theme_label = ttk.Label(theme_frame, text="主题：")
    theme_label.pack(side=tk.LEFT)

    theme_var = tk.StringVar(value="flatly")
    theme_options = list(style.theme_names()) + ["IKUN"]
    theme_menu = ttk.OptionMenu(theme_frame, theme_var, "flatly", *theme_options, command=lambda _: change_theme())
    theme_menu.pack(side=tk.LEFT)

    login_user()

    atexit.register(logout_user)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    online_users_label = ttk.Label(main_frame, text="在线用户: 加载中...", style="info.TLabel")
    online_users_label.pack(side=tk.BOTTOM, pady=(10, 0))

    update_online_users(root, online_users_label)

    def on_closing():
        logout_user()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()

if __name__ == '__main__':
    logging.info(f"当前软件版本: {CURRENT_VERSION}")
    create_gui()
    

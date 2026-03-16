#!/usr/bin/python3
# This script depends on PyUSB. You can get it with pip install pyusb.
# You will also need libusb installed
# Additionally, install TkinterDnD2 with: pip install tkinterdnd2
# ssky修改版 + 自动依赖安装功能

# -------------------------- 新增：自动安装依赖库 --------------------------
import subprocess
import sys

def install_dependency(package):
    """自动安装指定的Python库"""
    try:
        # 使用当前Python环境的pip安装依赖
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", package])
        print(f"✅ 成功安装/升级依赖：{package}")
    except subprocess.CalledProcessError:
        print(f"❌ 安装依赖 {package} 失败，请手动执行：pip3 install {package}")
        sys.exit(1)

# 检查并安装所有必需依赖（pyusb 安装后提供 usb 模块）
_dep_checks = [("usb", "pyusb"), ("tkinterdnd2", "tkinterdnd2")]
for import_name, pip_name in _dep_checks:
    try:
        __import__(import_name)
        print(f"✅ 已检测到依赖：{pip_name}")
    except ImportError:
        print(f"❌ 未检测到依赖：{pip_name}，正在自动安装...")
        install_dependency(pip_name)

# 安装 libusb 包（内含与 Python 架构匹配的 libusb 库，解决 Anaconda x86_64 与 Homebrew arm64 不兼容）
try:
    import libusb
    print("✅ 已检测到依赖：libusb")
except ImportError:
    print("❌ 未检测到 libusb，正在自动安装（用于 PyUSB 后端）...")
    install_dependency("libusb")
# -------------------------- 自动安装依赖结束 --------------------------

import usb.core
import usb.backend.libusb1
import usb.util
import struct
import time
import threading
import os
import glob
import webbrowser

from pathlib import Path


def _get_usb_backend():
    """获取 PyUSB 后端：优先使用 libusb 包中与 Python 架构匹配的库（解决 Anaconda/Homebrew 架构冲突）"""
    try:
        import libusb
        pkg_path = os.path.dirname(libusb.__file__)
        platform_dir = os.path.join(pkg_path, "_platform")
        lib_path = None
        if sys.platform == "darwin":
            macos_x64 = os.path.join(platform_dir, "_macos", "x64")
            if os.path.isdir(macos_x64):
                libs = glob.glob(os.path.join(macos_x64, "*", "libusb-1.0*.dylib"))
                if libs:
                    lib_path = libs[0]
        elif sys.platform.startswith("linux"):
            linux_x64 = os.path.join(platform_dir, "_linux", "x64")
            if os.path.isdir(linux_x64):
                libs = glob.glob(os.path.join(linux_x64, "libusb-1.0*.so"))
                if libs:
                    lib_path = libs[0]
        if lib_path and os.path.exists(lib_path):
            backend = usb.backend.libusb1.get_backend(find_library=lambda x, p=lib_path: p)
            if backend is not None:
                return backend
    except Exception:
        pass
    return None

# Import TkinterDnD instead of standard Tkinter
from tkinterdnd2 import DND_FILES, TkinterDnD
import tkinter as tk
from tkinter import filedialog
from tkinter import scrolledtext
from tkinter import ttk

CMD_ID_EXIT = 0
CMD_ID_LIST_OLD = 1
CMD_ID_FILE_RANGE = 2
CMD_ID_LIST = 3

CMD_TYPE_REQUEST = 0
CMD_TYPE_RESPONSE = 1
CMD_TYPE_ACK = 2

BUFFER_SEGMENT_DATA_SIZE = 0x100000

file_list = {}

def LOG(line):
    """线程安全：通过 root.after 在主线程中更新 GUI"""
    def _do_log():
        try:
            if 'text1' in globals() and text1.winfo_exists():
                if int(text1.index('end').split('.')[0]) - 1 > 1000:
                    text1.delete('1.0', tk.END)
                text1.insert(tk.END, line + '\n')
                text1.see(tk.END)
        except Exception:
            pass
    try:
        if 'root' in globals() and root.winfo_exists():
            root.after(0, _do_log)
    except Exception:
        pass

def process_file_range_command(data_size):
    global file_list

    LOG('File range')
    dev.write(0x01, struct.pack('<4sIII', b'DBI0', CMD_TYPE_ACK, CMD_ID_FILE_RANGE, data_size))

    file_range_header = dev.read(0x81, data_size)

    range_size = struct.unpack('<I', file_range_header[:4])[0]
    range_offset = struct.unpack('<Q', file_range_header[4:12])[0]
    nsp_name_len = struct.unpack('<I', file_range_header[12:16])[0]
    nsp_name = bytes(file_range_header[16:]).decode('utf-8')

    LOG('Range Size: {}, Range Offset: {}, Name len: {}, Name: {}'.format(range_size, range_offset, nsp_name_len, nsp_name))

    response_bytes = struct.pack('<4sIII', b'DBI0', CMD_TYPE_RESPONSE, CMD_ID_FILE_RANGE, range_size)
    dev.write(0x01, response_bytes)

    ack = bytes(dev.read(0x81, 16, timeout=0))
    magic = ack[:4]
    cmd_type = struct.unpack('<I', ack[4:8])[0]
    cmd_id = struct.unpack('<I', ack[8:12])[0]
    data_size = struct.unpack('<I', ack[12:16])[0]

    # LOG('Cmd Type: {}, Command id: {}, Data size: {}'.format(cmd_type, cmd_id, data_size))
    # LOG('Ack')

    if nsp_name not in file_list:
        LOG(f'错误：找不到文件 "{nsp_name}"')
        return
    with open(file_list[nsp_name].__str__(), 'rb') as f:
        f.seek(range_offset)

        curr_off = 0x0
        end_off = range_size
        read_size = BUFFER_SEGMENT_DATA_SIZE

        while curr_off < end_off:
            if curr_off + read_size >= end_off:
                read_size = end_off - curr_off

            buf = f.read(read_size)
            dev.write(0x01, data=buf, timeout=0)
            curr_off += read_size

def poll_commands():
    LOG('Entering command loop')
    while True:
        try:
            cmd_header = bytes(dev.read(0x81, 16, timeout=0))
            magic = cmd_header[:4]

            if magic != b'DBI0':
                continue

            cmd_type = struct.unpack('<I', cmd_header[4:8])[0]
            cmd_id = struct.unpack('<I', cmd_header[8:12])[0]
            data_size = struct.unpack('<I', cmd_header[12:16])[0]

            # LOG('Cmd Type: {}, Command id: {}, Data size: {}'.format(cmd_type, cmd_id, data_size))

            if cmd_id == CMD_ID_EXIT:
                process_exit_command()
            elif cmd_id == CMD_ID_FILE_RANGE:
                process_file_range_command(data_size)
            elif cmd_id == CMD_ID_LIST:
                process_list_command()
        except usb.core.USBError:
            LOG('Switch connection lost')
            connect_to_switch()

def process_exit_command():
    LOG('Exit')
    dev.write(0x01, struct.pack('<4sIII', b'DBI0', CMD_TYPE_RESPONSE, CMD_ID_EXIT, 0))
    root.after(0, root.quit)  # 在主线程中退出，避免 Tkinter 线程安全问题

def process_list_command():
    global file_list
    LOG('Get list')
    nsp_path_list = ""
    nsp_path_list_len = 0

    for i, (k, v) in enumerate(file_list.items()):
        nsp_path_list += k + '\n'

    nsp_path_list_bytes = nsp_path_list.encode('utf-8')
    nsp_path_list_len = len(nsp_path_list_bytes)

    dev.write(0x01, struct.pack('<4sIII', b'DBI0', CMD_TYPE_RESPONSE, CMD_ID_LIST, nsp_path_list_len))

    if nsp_path_list_len > 0:
        ack = bytes(dev.read(0x81, 16, timeout=0))
        magic = ack[:4]
        cmd_type = struct.unpack('<I', ack[4:8])[0]
        cmd_id = struct.unpack('<I', ack[8:12])[0]
        data_size = struct.unpack('<I', ack[12:16])[0]

        # LOG('Cmd Type: {}, Command id: {}, Data size: {}'.format(cmd_type, cmd_id, data_size))
        # LOG('Ack')

        dev.write(0x01, nsp_path_list_bytes)

def connect_to_switch():
    global text1
    global dev
    backend = _get_usb_backend()
    while True:
        try:
            dev = usb.core.find(idVendor=0x057E, idProduct=0x3000, backend=backend)
        except usb.core.NoBackendError:
            print("\n" + "="*60)
            print("错误：PyUSB 找不到 libusb 后端")
            print("请确保已安装 libusb 包：pip install libusb")
            print("="*60 + "\n")
            raise
        if dev is None:
            LOG('Waiting for switch...')
            time.sleep(1)
            continue

        break

def start_server():
    print(file_list)
    connect_to_switch()
    poll_commands()

def do_start_server():
    global server_thread
    global addFolderButton
    global addFilesButton
    global clearListButton
    global startServerButton

    addFolderButton['state'] = 'disabled'
    addFilesButton['state'] = 'disabled'
    clearListButton['state'] = 'disabled'
    startServerButton['state'] = 'disabled'

    server_thread = threading.Thread(target=start_server)
    server_thread.daemon = True
    server_thread.start()

def updateFileList():
    global flist1
    global file_list
    global startServerButton

    flist1.config(state=tk.NORMAL)
    flist1.delete('1.0', tk.END)
    if len(file_list) == 0:
        flist1.insert(tk.END, "拖拽文件到此处，或点击上方按钮添加 NSP/NSZ 文件", "placeholder")
    else:
        for i, (k, v) in enumerate(sorted(file_list.items()), start=1):
            flist1.insert(tk.END, f"{i}. {v}\n")
    flist1.config(state=tk.DISABLED)

    if len(file_list) > 0:
        startServerButton['state'] = 'normal'
    else:
        startServerButton['state'] = 'disabled'

def gui_choose_dir():
    global file_list
    dirname = filedialog.askdirectory()
    if not dirname:
        return
    d = Path(dirname)
    for file_path in [f for f in d.iterdir() if f.is_file() and f.name != '.DS_Store']:
        file_list[file_path.name] = file_path.resolve()

    updateFileList()

def gui_choose_files():
    global file_list
    filenames = filedialog.askopenfilenames()
    for file_path in filenames:
        d = Path(file_path)
        if d.name != '.DS_Store':
            file_list[d.name] = d.resolve()

    updateFileList()

def gui_clear_list():
    global file_list

    file_list.clear()
    updateFileList()

def drop(event):
    """Handle file or directory drop."""
    global file_list
    # event.data may contain multiple files separated by space or newline
    data = root.splitlist(event.data)
    for item in data:
        path = Path(item)
        if path.is_file() and path.name != '.DS_Store':
            file_list[path.name] = path.resolve()
        elif path.is_dir():
            for file_path in path.rglob('*'):  # Recursively add files in directories
                if file_path.is_file() and file_path.name != '.DS_Store':
                    file_list[file_path.name] = file_path.resolve()
    updateFileList()

def start_gui(start):
    global addFolderButton
    global addFilesButton
    global clearListButton
    global startServerButton
    global text1
    global flist1
    global root

    # 配色方案
    BG_COLOR = "#f0f2f5"
    CARD_BG = "#ffffff"
    PRIMARY_COLOR = "#00c853"       # 鲜明绿色，醒目易识别
    PRIMARY_HOVER = "#00a843"
    TEXT_COLOR = "#333333"
    BORDER_COLOR = "#e8e8e8"

    root = TkinterDnD.Tk()
    root.title('DBI 后端 - 简体中文版 | 开源作者: CAI (caihanlin.com)')
    root.geometry('960x720')
    root.minsize(820, 620)
    root.resizable(True, True)
    root.configure(bg=BG_COLOR)

    root.drop_target_register(DND_FILES)
    root.dnd_bind('<<Drop>>', drop)

    # 主容器
    main_frame = tk.Frame(root, bg=BG_COLOR, padx=20, pady=16)
    main_frame.pack(fill=tk.BOTH, expand=True)

    # 使用说明卡片
    tip_card = tk.Frame(main_frame, bg='#e8f5e9', padx=14, pady=12, relief=tk.FLAT, highlightbackground='#81c784', highlightthickness=1)
    tip_card.pack(fill=tk.X, pady=(0, 14))

    tip_title = tk.Label(tip_card, text='📌 使用前请确认', bg='#e8f5e9', fg='#2e7d32', font=('', 10, 'bold'))
    tip_title.pack(anchor=tk.W)

    tip_text = (
        "1. 在 Switch 相册中打开 DBI 插件，选择「后端」选项\n"
        "2. 使用 USB 数据线连接 Switch 与电脑（请使用支持数据传输的数据线，非仅充电线）\n"
        "3. 等待 Switch 显示「等待 dbi 后端运行」后再点击「启动服务」"
    )
    tip_label = tk.Label(tip_card, text=tip_text, bg='#e8f5e9', fg='#388e3c', font=('', 9), justify=tk.LEFT)
    tip_label.pack(anchor=tk.W, pady=(6, 0))

    # 工具栏
    toolbar_frame = tk.Frame(main_frame, bg=BG_COLOR)
    toolbar_frame.pack(fill=tk.X, pady=(0, 12))

    addFolderButton = tk.Button(
        toolbar_frame, text='📁 添加文件夹', command=gui_choose_dir,
        width=14, height=2, cursor='hand2', relief=tk.FLAT, bg=CARD_BG,
        fg=TEXT_COLOR, font=('', 10), bd=0,
        activebackground='#e6f7ff', activeforeground=PRIMARY_COLOR
    )
    addFolderButton.pack(side=tk.LEFT, padx=(0, 8))

    addFilesButton = tk.Button(
        toolbar_frame, text='📄 添加文件', command=gui_choose_files,
        width=14, height=2, cursor='hand2', relief=tk.FLAT, bg=CARD_BG,
        fg=TEXT_COLOR, font=('', 10), bd=0,
        activebackground='#e6f7ff', activeforeground=PRIMARY_COLOR
    )
    addFilesButton.pack(side=tk.LEFT, padx=(0, 8))

    clearListButton = tk.Button(
        toolbar_frame, text='🗑 清空列表', command=gui_clear_list,
        width=12, height=2, cursor='hand2', relief=tk.FLAT, bg=CARD_BG,
        fg=TEXT_COLOR, font=('', 10), bd=0,
        activebackground='#fff2f0', activeforeground='#ff4d4f'
    )
    clearListButton.pack(side=tk.LEFT)

    # 文件列表区域
    file_card = tk.Frame(main_frame, bg=CARD_BG, padx=12, pady=10, relief=tk.FLAT, highlightbackground=BORDER_COLOR, highlightthickness=1)
    file_card.pack(fill=tk.BOTH, expand=True, pady=(0, 12))

    file_label = tk.Label(
        file_card, text='文件列表', bg=CARD_BG, fg=TEXT_COLOR,
        font=('', 11, 'bold')
    )
    file_label.pack(anchor=tk.W, pady=(0, 8))

    flist1 = scrolledtext.ScrolledText(
        file_card, height=16, wrap=tk.WORD, font=('', 10),
        bg='#fafafa', fg=TEXT_COLOR, insertbackground=TEXT_COLOR,
        relief=tk.FLAT, padx=12, pady=10, bd=0,
        highlightthickness=1, highlightbackground=BORDER_COLOR, highlightcolor=PRIMARY_COLOR
    )
    flist1.pack(fill=tk.BOTH, expand=True)
    flist1.tag_config('placeholder', foreground='#999999', font=('', 11))

    # 操作区
    action_frame = tk.Frame(main_frame, bg=BG_COLOR)
    action_frame.pack(fill=tk.X, pady=(0, 12))

    startServerButton = tk.Button(
        action_frame, text='▶ 启动服务', state=tk.DISABLED, command=do_start_server,
        width=16, height=2, cursor='hand2', relief=tk.FLAT,
        bg=PRIMARY_COLOR, fg='white', font=('', 11, 'bold'), bd=0,
        activebackground=PRIMARY_HOVER, activeforeground='white',
        highlightbackground=PRIMARY_COLOR, highlightcolor=PRIMARY_HOVER
    )
    startServerButton.pack(side=tk.LEFT)

    # 日志区域
    log_card = tk.Frame(main_frame, bg=CARD_BG, padx=12, pady=10, relief=tk.FLAT, highlightbackground=BORDER_COLOR, highlightthickness=1)
    log_card.pack(fill=tk.BOTH, expand=True)

    log_label = tk.Label(
        log_card, text='运行日志', bg=CARD_BG, fg=TEXT_COLOR,
        font=('', 11, 'bold')
    )
    log_label.pack(anchor=tk.W, pady=(0, 8))

    text1 = scrolledtext.ScrolledText(
        log_card, height=8, wrap=tk.WORD, font=('', 9),
        bg='#1e1e1e', fg='#d4d4d4', insertbackground='#d4d4d4',
        relief=tk.FLAT, padx=12, pady=10, bd=0,
        highlightthickness=1, highlightbackground=BORDER_COLOR
    )
    text1.pack(fill=tk.BOTH, expand=True)

    # 开源作者信息（点击可打开链接）
    author_frame = tk.Frame(main_frame, bg=BG_COLOR)
    author_frame.pack(fill=tk.X, pady=(10, 0))
    author_label = tk.Label(
        author_frame, text='开源作者: CAI (Hanlin Cai) · https://caihanlin.com/',
        bg=BG_COLOR, fg='#666666', font=('', 9), cursor='hand2'
    )
    author_label.pack()
    author_label.bind('<Button-1>', lambda e: webbrowser.open('https://caihanlin.com/'))

    if start:
        updateFileList()
        do_start_server()
    else:
        updateFileList()

    root.mainloop()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        start_gui(False)
    else:
        for filename in sys.argv[1:]:
            d = Path(filename)
            if d.is_file() and d.name != '.DS_Store':
                file_list[d.name] = d.resolve()
            elif d.is_dir():
                for file_path in d.rglob('*'):
                    if file_path.is_file() and file_path.name != '.DS_Store':
                        file_list[file_path.name] = file_path.resolve()

        start_gui(True)
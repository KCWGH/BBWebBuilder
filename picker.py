"""Show a native file dialog in its own GUI process, without a console window."""
import json
import sys
import tkinter as tk
from tkinter import filedialog


def main():
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    try:
        path = filedialog.askopenfilename(
            parent=root, title='BlackBerry JDP 프로젝트 열기',
            filetypes=[('BlackBerry 프로젝트', '*.jdp')],
        )
        sys.stdout.buffer.write(json.dumps({'path': path}, ensure_ascii=False).encode('utf-8'))
    finally:
        root.destroy()


if __name__ == '__main__':
    main()

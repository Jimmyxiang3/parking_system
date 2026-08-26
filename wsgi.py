# -*- coding: utf-8 -*-
"""WSGI 入口（生产环境用 gunicorn 启动）：
    gunicorn -w 2 -b 0.0.0.0:$PORT wsgi:app
"""
from app import app

if __name__ == '__main__':
    app.run()

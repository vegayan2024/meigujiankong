@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
python -m pip install -r requirements.txt
python -m streamlit run app.py
pause

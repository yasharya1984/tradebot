# 1. Install Python 3.10+ if needed, then:
pip install -r requirements.txt

# 2. Launch the dashboard (recommended starting point)
python main.py dashboard
# Opens at http://localhost:8501

# OR run individual commands:
python main.py screener    # See top momentum stocks
python main.py backtest    # Compare all 3 strategies
python main.py paper       # One cycle of paper trading

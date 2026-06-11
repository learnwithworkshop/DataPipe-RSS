# DataPipe-RSS 🗞️
Modular Python RSS-to-Spreadsheet automation pipeline.

## Setup
```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your webhook URLs
```

## Run
```bash
python main.py              # single run
python main.py --schedule   # run every hour
python main.py --stats      # show DB stats
```

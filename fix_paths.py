import os

def replace_in_file(filepath, old, new):
    with open(filepath, 'r') as f:
        content = f.read()
    content = content.replace(old, new)
    with open(filepath, 'w') as f:
        f.write(content)

replace_in_file("backend/main.py", '"../_downloads/content"', 'os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_downloads", "content")')
replace_in_file("backend/main.py", '"../_downloads/media"', 'os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_downloads", "media")')
replace_in_file("backend/main.py", '"../_downloads"', 'os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_downloads")')
replace_in_file("backend/main.py", '"sqlite:///../soupre.db"', f'"sqlite:///{os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "soupre.db")}"')

replace_in_file("backend/cli.py", '"sqlite:///../soupre.db"', f'"sqlite:///{os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "soupre.db")}"')

replace_in_file("backend/modules/scrapers/engine.py", '"../_downloads/markdown"', 'os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "_downloads", "content")')
replace_in_file("backend/modules/scrapers/engine.py", '"../_downloads/media"', 'os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "_downloads", "media")')

replace_in_file("backend/modules/scrapers/fetcher.py", '"../_downloads/media', 'os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "_downloads", "media"')

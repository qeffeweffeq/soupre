import pandas as pd
import requests
import os

def fetch_nga_turner():
    os.makedirs('nga_images', exist_ok=True)
    print("Loading NGA Open Data via Pandas...")
    
    objects_url = "https://raw.githubusercontent.com/NationalGalleryOfArt/opendata/main/data/objects.csv"
    images_url = "https://raw.githubusercontent.com/NationalGalleryOfArt/opendata/main/data/published_images.csv"
    
    df_obj = pd.read_csv(objects_url, usecols=['objectid', 'title', 'attribution'])
    df_img = pd.read_csv(images_url, usecols=['objectid', 'iiifthumburl'])
    
    # Filter for Turner
    turner_works = df_obj[df_obj['attribution'].str.contains("Joseph Mallord William Turner", na=False)]
    
    # Join with images to get IIIF URLs
    merged = pd.merge(turner_works, df_img, on='objectid')
    
    for _, row in merged.iterrows():
        title = str(row['title']).replace("/", "-")
        # Convert thumb URL to max res IIIF URL
        base_iiif = str(row['iiifthumburl']).split('/full/')[0]
        max_res_url = f"{base_iiif}/full/max/0/default.jpg"
        
        filename = f"nga_images/{title}.jpg"
        if not os.path.exists(filename):
            print(f"Downloading: {title}")
            resp = requests.get(max_res_url)
            if resp.status_code == 200:
                with open(filename, 'wb') as f:
                    f.write(resp.content)

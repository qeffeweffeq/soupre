import requests
import time
import os
import cv2

def fetch_and_stitch_faa(artwork_id):
    width_medium = 900
    height_medium = 593
    os.makedirs('faa_tiles', exist_ok=True)
    
    tile_paths = []

    # 1. Fetch overlapping tiles
    for x in range(0, width_medium + 1, 65):
        for y in range(0, height_medium + 1, 65):
            url = f"https://render.fineartamerica.com/previewhighresolutionimage.php?artworkid={artwork_id}&widthmedium={width_medium}&heightmedium={height_medium}&x={x}&y={y}&domainUrl=fineartamerica.com"
            filename = f"faa_tiles/tile_{x}_{y}.jpg"
            if not os.path.exists(filename):
                resp = requests.get(url)
                if resp.status_code == 200:
                    with open(filename, 'wb') as f:
                        f.write(resp.content)
                time.sleep(0.5)
            tile_paths.append(filename)

    # 2. Stitch using cv2.Stitcher for best results
    print("Stitching tiles with OpenCV...")
    images = [cv2.imread(p) for p in tile_paths if os.path.exists(p)]
    stitcher = cv2.Stitcher_create()
    status, stitched = stitcher.stitch(images)
    
    if status == cv2.Stitcher_OK:
        cv2.imwrite("faa_final.jpg", stitched)
        print("Successfully stitched FAA image.")
    else:
        print(f"Stitching failed with status: {status}")

import os
import rasterio
import numpy as np
from rasterio.merge import merge
from rasterio.enums import Resampling
from rasterio.warp import reproject
from scipy.ndimage import gaussian_filter

def calcular_ndvi(band_nir_path, band_red_path):
    with rasterio.open(band_nir_path) as nir_src, rasterio.open(band_red_path) as red_src:
        nir_data = nir_src.read(1).astype('float32')
        nir_transform = nir_src.transform
        nir_shape = nir_data.shape

        red_data_resampled = np.empty(nir_shape, dtype='float32')

        # Reescalado de la banda RED con interpolación cúbica (mayor calidad)
        reproject(
            source=red_src.read(1),
            destination=red_data_resampled,
            src_transform=red_src.transform,
            src_crs=red_src.crs,
            dst_transform=nir_transform,
            dst_crs=nir_src.crs,
            resampling=Resampling.cubic
        )

        # Cálculo del NDVI
        ndvi = np.where(
            (nir_data + red_data_resampled) == 0,
            0,
            (nir_data - red_data_resampled) / (nir_data + red_data_resampled)
        )

        # Suavizado opcional con filtro Gaussiano
        ndvi = gaussian_filter(ndvi, sigma=1)

        # Perfil del raster
        profile = nir_src.profile.copy()
        profile.update({
            'driver': 'GTiff',
            'dtype': 'float32',
            'count': 1,
            'compress': 'lzw',
            'tiled': True,
            'blockxsize': 256,
            'blockysize': 256
        })

        return ndvi, profile

def guardar_ndvi(ndvi_array, profile, output_path):
    """Guarda el NDVI en un archivo GeoTIFF"""
    with rasterio.open(output_path, 'w', **profile) as dst:
        dst.write(ndvi_array, 1)

def procesar_ndvi_por_tiles(organized_bands, output_folder="NDVI_output"):
    """
    Recorre los tiles extraídos por el CopernicusDownloader y calcula el NDVI de cada uno.
    También puede unir los NDVI en un solo mosaico si hay más de uno.
    """
    os.makedirs(output_folder, exist_ok=True)
    ndvi_files = []

    for tile_id, info in organized_bands.items():
        bands = info["bands"]
        if "B08" in bands and "B04" in bands:
            ndvi, profile = calcular_ndvi(bands["B08"], bands["B04"])

            output_path = os.path.join(output_folder, f"{tile_id}_NDVI.tif")
            guardar_ndvi(ndvi, profile, output_path)
            ndvi_files.append(output_path)
            print(f"✓ NDVI calculado para tile {tile_id} → {output_path}")
        else:
            print(f"✗ Tile {tile_id} no tiene ambas bandas necesarias")

    # Unir NDVIs si hay más de uno
    if len(ndvi_files) > 1:
        sources = [rasterio.open(fp) for fp in ndvi_files]
        mosaic, out_trans = merge(sources)

        mosaic_profile = sources[0].profile
        mosaic_profile.update(
            height=mosaic.shape[1],
            width=mosaic.shape[2],
            transform=out_trans,
            count=1
        )

        output_mosaic = os.path.join(output_folder, "NDVI_MOSAICO.tif")
        with rasterio.open(output_mosaic, 'w', **mosaic_profile) as dst:
            dst.write(mosaic)

        print(f"✓ Mosaico NDVI generado: {output_mosaic}")
    else:
        print("No hay suficientes tiles para mosaico.")
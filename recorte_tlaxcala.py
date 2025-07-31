# recorte_tlaxcala.py
import os
import zipfile
import geopandas as gpd
import rasterio
import rasterio.mask
import shutil

def descomprimir_shapefile(zip_path, output_dir="shapes"):
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(output_dir)
    return output_dir

def filtrar_estado(gdf, nombre_estado="TLAXCALA"):
    return gdf[gdf['ENTIDAD'].str.upper() == nombre_estado.upper()]

def recortar_raster_con_shapefile(raster_path, shapefile_path, output_path):
    gdf = gpd.read_file(shapefile_path)
    with rasterio.open(raster_path) as src:
        if gdf.crs != src.crs:
            gdf = gdf.to_crs(src.crs)

        geometries = gdf.geometry.values
        out_image, out_transform = rasterio.mask.mask(src, geometries, crop=True)
        out_meta = src.meta.copy()

        out_meta.update({
            "driver": "GTiff",
            "height": out_image.shape[1],
            "width": out_image.shape[2],
            "transform": out_transform
        })

        with rasterio.open(output_path, "w", **out_meta) as dest:
            dest.write(out_image)

    print(f"✓ NDVI recortado guardado en: {output_path}")

def recortar_ndvi_con_tlaxcala(zip_shapefile, ndvi_path, salida_path="NDVI_TLAXCALA.tif"):
    tmp_dir = "shapefile_tmp"
    descomprimir_shapefile(zip_shapefile, tmp_dir)

    shp_files = [f for f in os.listdir(tmp_dir) if f.endswith(".shp")]
    if not shp_files:
        raise FileNotFoundError("No se encontró ningún .shp en el ZIP")
    shp_path = os.path.join(tmp_dir, shp_files[0])

    gdf = gpd.read_file(shp_path)
    gdf_tlaxcala = filtrar_estado(gdf, "TLAXCALA")

    if gdf_tlaxcala.empty:
        raise ValueError("No se encontró el estado TLAXCALA en el shapefile")

    tlax_path = os.path.join(tmp_dir, "tlaxcala.shp")
    gdf_tlaxcala.to_file(tlax_path)

    recortar_raster_con_shapefile(ndvi_path, tlax_path, salida_path)

    shutil.rmtree(tmp_dir)

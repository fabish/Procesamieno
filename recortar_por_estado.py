import os
import zipfile
import geopandas as gpd
import rasterio
import rasterio.mask
import shutil

def descomprimir_shapefile(zip_path, output_dir="shapes"):
    """Descomprime un ZIP de shapefiles"""
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(output_dir)
    return output_dir

def filtrar_estado(gdf, nombre_estado="TLAXCALA"):
    """Filtra el GeoDataFrame por el nombre del estado"""
    return gdf[gdf['ENTIDAD'].str.upper() == nombre_estado.upper()]

def recortar_raster_con_shapefile(raster_path, shapefile_path, output_path):
    """Recorta un raster usando un shapefile y guarda la salida"""
    gdf = gpd.read_file(shapefile_path)

    # Asegurar CRS compatible
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

    print(f"✓ Recorte guardado en: {output_path}")

def recortar_ndvi_con_tlaxcala(zip_shapefile, ndvi_path, salida_path="NDVI_TLAXCALA.tif"):
    """Proceso completo"""
    tmp_dir = "shapefile_tmp"

    # Paso 1: Descomprimir shapefile
    descomprimir_shapefile(zip_shapefile, tmp_dir)

    # Paso 2: Buscar archivo .shp
    shp_files = [f for f in os.listdir(tmp_dir) if f.endswith(".shp")]
    if not shp_files:
        raise FileNotFoundError("No se encontró ningún .shp en el ZIP")
    shp_path = os.path.join(tmp_dir, shp_files[0])

    # Paso 3: Filtrar Tlaxcala
    gdf = gpd.read_file(shp_path)
    gdf_tlaxcala = filtrar_estado(gdf, "TLAXCALA")

    if gdf_tlaxcala.empty:
        raise ValueError("No se encontró el estado TLAXCALA en el shapefile")

    # Paso 4: Guardar shapefile filtrado (opcional)
    tlax_path = os.path.join(tmp_dir, "tlaxcala.shp")
    gdf_tlaxcala.to_file(tlax_path)

    # Paso 5: Recortar raster
    recortar_raster_con_shapefile(ndvi_path, tlax_path, salida_path)

    # Limpieza opcional
    shutil.rmtree(tmp_dir)

# ▶️ Ejemplo de uso
if __name__ == "__main__":
    zip_shp = "Estados_Mexico.zip"             # ← Cambia si tu ZIP tiene otro nombre
    ndvi_tif = "NDVI_output/NDVI_MOSAICO.tif"   # ← O cualquier TIFF de entrada
    salida = "NDVI_TLAXCALA.tif"
    recortar_ndvi_con_tlaxcala(zip_shp, ndvi_tif, salida)

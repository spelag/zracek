"""
karta.py - hiter pregled ocenjenih H3 celic na karti.
Zagon: python karta.py   -> ustvari karta.html in jo odpre v brskalniku.
Potrebuje internet (podlaga karte in deck.gl se naložita s spleta).
"""
import webbrowser
import pandas as pd
import geopandas as gpd
import pydeck as pdk
from ocena import najdi

df = pd.read_csv("ocena_21h.csv")

# barva: rumena (nizka ocena) -> rdeča (visoka)
t = (df.ocena - df.ocena.min()) / (df.ocena.max() - df.ocena.min() + 1e-9)
df["r"] = 255
df["g"] = (230 * (1 - t)).astype(int)
df["b"] = 0
df["rank"] = range(1, len(df) + 1)
for c in ("ocena", "pm", "dist_postaja_m"):
    df[c] = df[c].round(2)

hex_layer = pdk.Layer(
    "H3HexagonLayer", df.iloc[::-1],   # najboljše narisano zadnje (na vrhu)
    get_hexagon="cell",
    get_fill_color="[r, g, b, 150]",
    get_line_color=[80, 80, 80],
    line_width_min_pixels=1,
    pickable=True, extruded=False,
)

top_layer = pdk.Layer(
    "H3HexagonLayer", df.head(10),
    get_hexagon="cell", filled=False, stroked=True,
    get_line_color=[200, 0, 120], line_width_min_pixels=4, pickable=True,
)

# obstoječe uradne postaje (ARSO + EEA)
st = pd.concat([
    gpd.read_file(najdi("arso_lj_postaje.geojson"))[["geometry"]],
    gpd.read_file(najdi("eea_postaje.geojson"))[["geometry"]],
])
st["lon"], st["lat"] = st.geometry.x, st.geometry.y
st_layer = pdk.Layer(
    "ScatterplotLayer", st[["lon", "lat"]],
    get_position="[lon, lat]", get_radius=150,
    get_fill_color=[0, 90, 255, 230], pickable=False,
)

deck = pdk.Deck(
    layers=[hex_layer, top_layer, st_layer],
    initial_view_state=pdk.ViewState(latitude=46.056, longitude=14.505, zoom=10.5),
    map_style="light",
    tooltip={"text": "Mesto #{rank}  (ocena {ocena})\nuporabnikov: {n}\n"
                     "PM2.5 (model): {pm}\ndo postaje: {dist_postaja_m} m\n"
                     "občutljivih lokacij: {obcutljive}"},
)
deck.to_html("karta.html", open_browser=False)
webbrowser.open("karta.html")
print("Karta shranjena v karta.html")
"""An automatically registered, local sidebar view; no dashboard YAML required."""
from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig


async def async_setup_panel(hass):
    data = hass.data.setdefault("hive_virtual_panel", {})
    if data.get("registered"):
        return
    # Avoid overwriting an existing user-created dashboard with the same path.
    path = "hive-virtual-heating"
    if not data.get("static_registered"):
        await hass.http.async_register_static_paths([StaticPathConfig(
            "/hive_virtual/frontend", str(Path(__file__).parent / "frontend"), False),
            StaticPathConfig("/hive_virtual/brand", str(Path(__file__).parent / "brand"), True)])
        data["static_registered"] = True
    await panel_custom.async_register_panel(hass,
        webcomponent_name="hive-virtual-panel", frontend_url_path=path,
        sidebar_title="Hive Heating", sidebar_icon="mdi:radiator",
        module_url="/hive_virtual/frontend/panel.js?v=0.6.2", require_admin=False)
    data["registered"] = True


def async_remove_panel(hass):
    frontend.async_remove_panel(hass, "hive-virtual-heating")
    hass.data["hive_virtual_panel"]["registered"] = False

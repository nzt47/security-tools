# plugins/example.py
from flask import Blueprint, jsonify
from .plugin_api import Plugin, register_plugin

bp = Blueprint("example", __name__)

@bp.route("/api/example/plugin-probe")
def api_example_probe():
    return jsonify({"plugin": "example", "ok": True})

PLUGIN = register_plugin(Plugin(
    name="example",
    version="0.1.0",
    description="插件机制探测",
    blueprint=bp,
    routes=["/api/example/plugin-probe"],
))

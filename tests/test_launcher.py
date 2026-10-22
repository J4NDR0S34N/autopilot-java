"""Pruebas del asistente autopilot.py (sin tocar la configuración real del usuario)."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("autopilot_cli", Path(__file__).resolve().parent.parent / "autopilot.py")
ap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ap)


class RegisterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._cfg = ap.opencode_config_path
        ap.opencode_config_path = lambda: self.tmp / "opencode.json"

    def tearDown(self):
        ap.opencode_config_path = self._cfg

    def test_opencode_conserva_claves_y_hace_copia(self):
        path = self.tmp / "opencode.json"
        path.write_text(json.dumps({"theme": "dark", "mcp": {"otro": {"type": "local"}}}), encoding="utf-8")
        self.assertTrue(ap.register_opencode("opencode/big-pickle"))
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["theme"], "dark")
        self.assertIn("otro", data["mcp"])
        self.assertEqual(data["mcp"][ap.SERVER_NAME]["command"][1:], ["-m", "netbeans_autopilot"])
        self.assertEqual(data["model"], "opencode/big-pickle")
        self.assertTrue((self.tmp / "opencode.json.bak").exists())

    def test_opencode_no_toca_jsonc(self):
        path = self.tmp / "opencode.json"
        original = '{\n  // comentario\n  "theme": "dark"\n}\n'
        path.write_text(original, encoding="utf-8")
        self.assertFalse(ap.register_opencode(None))
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_opencode_sin_modelo_no_pisa_el_existente(self):
        path = self.tmp / "opencode.json"
        path.write_text(json.dumps({"model": "anthropic/x"}), encoding="utf-8")
        ap.register_opencode(None)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["model"], "anthropic/x")


class ChecksTests(unittest.TestCase):
    def test_doctor_devuelve_todas_las_comprobaciones(self):
        names = [c.name for c in ap.check_all()]
        for expected in ("Python 3.10+", "Git", "JDK (javac)", "Paquete MCP", "Carpeta de proyectos"):
            self.assertIn(expected, names)


if __name__ == "__main__":
    unittest.main()

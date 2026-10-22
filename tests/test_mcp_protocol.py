"""Prueba E: el servidor arranca, habla MCP por stdio, publica sus herramientas y notifica errores.

Ejecutar:  python -m unittest tests.test_mcp_protocol -v
"""
import asyncio
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

from tests import fixtures  # noqa: E402

REPO = fixtures.REPO
EXPECTED_TOOLS = {
    "get_environment", "list_projects", "inspect_project", "read_project_file", "search_project_code",
    "get_project_status", "create_project", "create_project_file", "edit_project_file",
    "apply_project_changes", "validate_project_changes", "revert_project_changes", "get_change_summary",
    "detect_build_system", "compile_project", "run_project", "run_project_tests", "get_build_errors",
    "inspect_runtime_logs"}


def server_env(cfgfile: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("NB_AUTOPILOT_ROOT", "NB_AUTOPILOT_DATA")}
    env["NB_AUTOPILOT_CONFIG"] = str(cfgfile)
    return env


class TestE_MCP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.build_sandbox()
        cls.cfgfile = fixtures.SANDBOX / "config.json"
        cls.cfgfile.write_text(json.dumps(fixtures.sandbox_config()), encoding="utf-8")

    def _session(self, coro):
        params = StdioServerParameters(command=sys.executable, args=["-m", "netbeans_autopilot"],
                                       cwd=str(REPO), env=server_env(self.cfgfile))

        async def go():
            async with stdio_client(params) as (r, w):
                async with ClientSession(r, w) as s:
                    init = await s.initialize()
                    return await coro(s, init)

        return asyncio.run(asyncio.wait_for(go(), 120))

    def test_descubre_herramientas_y_responde(self):
        async def scenario(s, init):
            tools = await s.list_tools()
            names = {t.name for t in tools.tools}
            ro = {t.name: t.annotations.readOnlyHint for t in tools.tools}
            lp = await s.call_tool("list_projects", {})
            return init, names, ro, lp

        init, names, ro, lp = self._session(scenario)
        self.assertEqual(init.serverInfo.name, "netbeans-autopilot")
        self.assertEqual(names, EXPECTED_TOOLS)
        self.assertTrue(ro["list_projects"])
        self.assertFalse(ro["compile_project"])
        self.assertFalse(lp.isError)
        data = json.loads(lp.content[0].text)
        self.assertEqual(data["count"], 5)

    def test_errores_se_informan_como_isError(self):
        async def scenario(s, init):
            outside = await s.call_tool("read_project_file", {"project": "HolaMaven", "path": "../../x.txt"})
            missing = await s.call_tool("inspect_project", {"project": "NoExiste"})
            badargs = await s.call_tool("read_project_file", {"project": "HolaMaven"})
            unknown = await s.call_tool("herramienta_inexistente", {})
            protected = await s.call_tool("create_project_file", {
                "project": "Intocable", "path": "src/A.java", "content": "class A{}"})
            return outside, missing, badargs, unknown, protected

        outside, missing, badargs, unknown, protected = self._session(scenario)
        for name, r in (("fuera de raíz", outside), ("proyecto inexistente", missing),
                        ("argumentos inválidos", badargs), ("herramienta desconocida", unknown),
                        ("proyecto solo lectura", protected)):
            with self.subTest(caso=name):
                self.assertTrue(r.isError, f"{name} debía ser isError")
        self.assertIn("fuera de la carpeta autorizada", outside.content[0].text)
        self.assertIn("solo lectura", protected.content[0].text)
        self.assertFalse((fixtures.ROOT / "Intocable/src/A.java").exists())

    def test_flujo_completo_por_protocolo(self):
        async def scenario(s, init):
            ins = await s.call_tool("inspect_project", {"project": "RotoMaven"})
            c1 = await s.call_tool("compile_project", {"project": "RotoMaven"})
            ed = await s.call_tool("edit_project_file", {
                "project": "RotoMaven", "path": "src/main/java/demo/Main.java",
                "old_string": "totl", "new_string": "total"})
            c2 = await s.call_tool("compile_project", {"project": "RotoMaven"})
            run = await s.call_tool("run_project", {"project": "RotoMaven"})
            sm = await s.call_tool("get_change_summary", {"project": "RotoMaven"})
            return ins, c1, ed, c2, run, sm

        ins, c1, ed, c2, run, sm = self._session(scenario)
        j = lambda r: json.loads(r.content[0].text)  # noqa: E731
        self.assertEqual(j(ins)["build"]["primary"], "maven")
        self.assertFalse(j(c1)["success"])
        self.assertEqual(j(c1)["diagnostics"]["error_count"], 1)
        self.assertEqual(j(ed)["changes"][0]["action"], "modify")
        self.assertTrue(j(c2)["success"])
        self.assertEqual(j(run)["stdout"].strip(), "5")
        self.assertEqual(j(sm)["modified"], ["RotoMaven/src/main/java/demo/Main.java"])

    def test_sin_configuracion_falla_con_mensaje_claro(self):
        env = {k: v for k, v in os.environ.items() if k not in ("NB_AUTOPILOT_ROOT", "NB_AUTOPILOT_DATA")}
        env["NB_AUTOPILOT_CONFIG"] = str(fixtures.SANDBOX / "no_existe.json")
        p = subprocess.run([sys.executable, "-m", "netbeans_autopilot"], cwd=str(REPO), env=env,
                           capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual(p.returncode, 2)
        self.assertIn("carpeta raíz", p.stderr.decode("utf-8"))
        self.assertEqual(p.stdout, b"")        # stdout reservado al protocolo


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Pruebas de aceptación A, B, C, D y F (usan la API interna sobre un sandbox aislado).

Ejecutar:  python -m unittest tests.test_acceptance -v
"""
import hashlib
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import fixtures  # noqa: E402

from netbeans_autopilot import edits, fileio, projects  # noqa: E402
from netbeans_autopilot.build import Builder  # noqa: E402
from netbeans_autopilot.config import load_config  # noqa: E402
from netbeans_autopilot.security import AutopilotError, Guard, SecurityError  # noqa: E402

REAL_ROOT = Path(os.environ.get("NB_REAL_ROOT", str(Path.home() / "Documents" / "NetBeansProjects")))


def manifest(root: Path) -> dict:
    out = {}
    if not root.exists():
        return out
    for p in root.rglob("*"):
        if p.is_file():
            out[str(p)] = (p.stat().st_size, hashlib.sha256(p.read_bytes()).hexdigest())
    return out


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.real_before = manifest(REAL_ROOT)
        info = fixtures.build_sandbox()
        cls.junction = info["junction_created"]
        cfgfile = fixtures.SANDBOX / "config.json"
        cfgfile.write_text(json.dumps(fixtures.sandbox_config()), encoding="utf-8")
        os.environ["NB_AUTOPILOT_CONFIG"] = str(cfgfile)
        os.environ.pop("NB_AUTOPILOT_ROOT", None)
        os.environ.pop("NB_AUTOPILOT_DATA", None)
        cls.cfg = load_config()
        cls.guard = Guard(cls.cfg)
        cls.store = fileio.ChangeStore(cls.cfg.data_dir)
        cls.ed = edits.Editor(cls.guard, cls.store)
        cls.b = Builder(cls.cfg, cls.guard)

    @classmethod
    def tearDownClass(cls):
        after = manifest(REAL_ROOT)
        assert after == cls.real_before, "¡Se modificaron proyectos reales durante las pruebas!"


class TestA_Acceso(Base):
    def test_detecta_raiz_y_herramientas(self):
        self.assertEqual(self.cfg.root, Path(os.path.realpath(fixtures.ROOT)))
        self.assertIsNotNone(self.cfg.java_home, "JDK no encontrado")
        self.assertIsNotNone(self.cfg.maven_cmd, "Maven no encontrado")

    def test_enumera_proyectos(self):
        r = projects.list_projects(self.cfg)
        names = {p["name"]: p for p in r["projects"]}
        self.assertEqual(set(names), {"HolaMaven", "RotoMaven", "SimpleJava", "SwingApp", "Intocable"})
        self.assertEqual(names["HolaMaven"]["build_system"], "maven")
        self.assertEqual(names["SimpleJava"]["build_system"], "plain-java")
        self.assertTrue(names["Intocable"]["read_only"])
        self.assertEqual(r["loose_java_files_in_root"], ["Ejercicio1.java"])

    def test_inspecciona_proyecto_java(self):
        r = projects.inspect_project(self.cfg, self.guard, "SwingApp")
        self.assertEqual(r["build"]["primary"], "maven")
        self.assertIn("demo", r["packages"])
        self.assertIn("Ventana", r["packages"]["demo"])
        self.assertEqual(r["swing_components"][0]["extends"], "JFrame")
        self.assertTrue(r["netbeans_forms"][0]["java_exists"])
        self.assertIn("demo.Main", [e["class"] for e in r["entry_points"]])
        self.assertEqual(r["java_version_required"], 17)

    def test_lee_archivo_autorizado(self):
        r = self.ed.read("HolaMaven", "src/main/java/demo/Main.java")
        self.assertIn('"Hola Mundo"', r["content"])
        self.assertEqual(len(r["sha256"]), 64)

    def test_oculta_contrasenas(self):
        r = self.ed.read("HolaMaven", "src/main/resources/db.properties")
        self.assertNotIn("SuperSecreta123", r["content"])
        self.assertIn("REDACTED", r["content"])
        self.assertIn("jdbc:mysql://localhost/test", r["content"])

    def test_rechaza_rutas_fuera_de_la_raiz(self):
        for bad in ("../outside/secreto.txt", "..\\..\\Windows\\win.ini", "C:\\Windows\\win.ini",
                    "HolaMaven/../../outside/secreto.txt", "\\\\localhost\\c$\\x", "HolaMaven/a.txt:ads"):
            with self.subTest(ruta=bad):
                with self.assertRaises(SecurityError):
                    self.guard.resolve(bad)
        with self.assertRaises(SecurityError):
            self.ed.read("HolaMaven", "../RotoMaven/pom.xml")          # otro proyecto
        with self.assertRaises(SecurityError):
            self.ed.read("..", "x")
        with self.assertRaises(SecurityError):
            self.ed.read("HolaMaven/src", "x")

    def test_rechaza_union_que_escapa(self):
        if not self.junction:
            self.skipTest("No se pudo crear la unión NTFS en este entorno")
        with self.assertRaises(SecurityError):
            self.ed.read("HolaMaven", "escape/secreto.txt")
        with self.assertRaises(SecurityError):
            self.ed.apply("HolaMaven", [{"op": "create", "path": "escape/nuevo.txt", "content": "x"}])
        self.assertFalse((fixtures.OUTSIDE / "nuevo.txt").exists())


class TestB_Creacion(Base):
    def test_crear_compilar_ejecutar(self):
        code = ('package demo;\n\npublic class Saludo {\n    public static void main(String[] args) {\n'
                '        System.out.println("Suma: " + (2 + 3));\n    }\n}\n')
        r = self.ed.apply("HolaMaven", [{"op": "create", "path": "src/main/java/demo/Saludo.java", "content": code}])
        self.assertEqual(r["changes"][0]["action"], "create")
        comp = self.b.compile("HolaMaven")
        self.assertTrue(comp["success"], comp["output"])
        run = self.b.run("HolaMaven", main_class="demo.Saludo")
        self.assertTrue(run["ran"])
        self.assertEqual(run["exit_code"], 0)
        self.assertEqual(run["stdout"].strip(), "Suma: 5")

    def test_main_ambiguo_pide_clase(self):
        self.ed.apply("HolaMaven", [{"op": "create", "path": "src/main/java/demo/Otro.java",
                                      "content": "package demo;\n\npublic class Otro {\n"
                                                 "    public static void main(String[] a) {}\n}\n"}])
        with self.assertRaises(AutopilotError) as cm:
            self.b.run("HolaMaven")     # hay varias clases con main: debe pedir cuál
        self.assertIn("main_class", str(cm.exception))
        with self.assertRaises(AutopilotError):
            self.b.run("HolaMaven", main_class="demo.Main; calc.exe")   # nombre de clase inyectado

    def test_proyecto_simple_con_stdin(self):
        run = self.b.run("SimpleJava", main_class="Saludo", stdin_text="4\n-9\n")
        self.assertEqual(run["exit_code"], 0, run)
        self.assertEqual(run["stdout"].strip(), "-5")
        run0 = self.b.run("SimpleJava", main_class="Saludo", stdin_text="0\n0\n")
        self.assertEqual(run0["stdout"].strip(), "0")

    def test_excepcion_en_tiempo_de_ejecucion(self):
        run = self.b.run("SimpleJava", main_class="Saludo", stdin_text="abc\n")
        self.assertNotEqual(run["exit_code"], 0)
        logs = self.b.logs("SimpleJava")
        self.assertGreaterEqual(logs["exception_count"], 1)
        e = logs["exceptions"][0]
        self.assertIn("InputMismatchException", e["type"])
        self.assertEqual(e["origin"]["file"], "Saludo.java")
        self.assertEqual(e["origin"]["project_file"], "src/Saludo.java")


class TestC_Correccion(Base):
    def test_detecta_corrige_y_recompila(self):
        comp = self.b.compile("RotoMaven")
        self.assertFalse(comp["success"])
        errs = self.b.errors("RotoMaven")
        self.assertEqual(errs["error_count"], 1, errs)
        e = errs["errors"][0]
        self.assertEqual(e["file"], "src/main/java/demo/Main.java")
        self.assertEqual(e["line"], 6)
        self.assertIn("cannot find symbol", e["message"])
        self.assertTrue(any("totl" in d for d in e["details"]), e)
        # corrección
        self.ed.apply("RotoMaven", [{"op": "edit", "path": "src/main/java/demo/Main.java",
                                      "old_string": "println(totl)", "new_string": "println(total)"}])
        v = self.ed.validate("RotoMaven")
        self.assertTrue(v["valid"], v)
        comp2 = self.b.compile("RotoMaven")
        self.assertTrue(comp2["success"], comp2["output"])
        run = self.b.run("RotoMaven")
        self.assertEqual(run["stdout"].strip(), "5")


class TestD_Swing(Base):
    SMOKE = '''package demo;

import java.awt.GraphicsEnvironment;
import javax.swing.*;

public class SwingSmoke {
    public static void main(String[] args) throws Exception {
        if (GraphicsEnvironment.isHeadless()) { System.out.println("HEADLESS"); return; }
        final int[] clicks = {0};
        SwingUtilities.invokeAndWait(() -> {
            JFrame f = new JFrame("Prueba");
            JButton b = new JButton("Pulsar");
            b.addActionListener(e -> clicks[0]++);
            f.add(b);
            f.pack();
            f.setVisible(true);
            b.doClick();
            b.doClick();
            f.dispose();
        });
        System.out.println("CLICKS=" + clicks[0]);
        System.exit(clicks[0] == 2 ? 0 : 1);
    }
}
'''

    def test_compila_y_comporta_boton(self):
        self.ed.apply("SwingApp", [{"op": "create", "path": "src/main/java/demo/SwingSmoke.java",
                                     "content": self.SMOKE}])
        comp = self.b.compile("SwingApp")
        self.assertTrue(comp["success"], comp["output"])      # compila Ventana (con código del diseñador)
        run = self.b.run("SwingApp", main_class="demo.SwingSmoke", timeout=30)
        self.assertTrue(run["ran"])
        if run["stdout"].strip() == "HEADLESS":
            self.skipTest("Sin entorno gráfico: solo se verificó la compilación")
        self.assertEqual(run["exit_code"], 0, run)
        self.assertIn("CLICKS=2", run["stdout"])


class TestF_Proteccion(Base):
    def test_archivo_protegido_por_configuracion(self):
        with self.assertRaises(SecurityError):
            self.ed.apply("HolaMaven", [{"op": "edit", "path": "src/main/resources/protegido.properties",
                                          "old_string": "valor", "new_string": "otro"}])
        self.assertEqual((fixtures.ROOT / "HolaMaven/src/main/resources/protegido.properties").read_text(),
                         "clave=valor\n")

    def test_proyecto_solo_lectura(self):
        with self.assertRaises(SecurityError):
            self.ed.apply("Intocable", [{"op": "create", "path": "src/main/java/demo/X.java", "content": "class X{}"}])
        self.assertFalse((fixtures.ROOT / "Intocable/src/main/java/demo/X.java").exists())

    def test_carpetas_y_extensiones_protegidas(self):
        for path in ("target/classes/x.java", "nbproject/private/private.xml", ".git/config", "build/x.xml",
                     "src/main/java/demo/Evil.exe", "src/main/java/demo/run.bat"):
            with self.subTest(path=path), self.assertRaises(SecurityError):
                self.ed.apply("HolaMaven", [{"op": "create", "path": path, "content": "x"}])

    def test_no_se_borran_configuraciones(self):
        with self.assertRaises(SecurityError):
            self.ed.apply("HolaMaven", [{"op": "delete", "path": "pom.xml", "confirm": True}])
        self.assertTrue((fixtures.ROOT / "HolaMaven/pom.xml").exists())

    def test_no_sobrescribe_sin_permiso(self):
        with self.assertRaises(AutopilotError):
            self.ed.apply("HolaMaven", [{"op": "create", "path": "src/main/java/demo/Main.java", "content": "x"}])

    def test_codigo_del_disenador_protegido(self):
        path = "src/main/java/demo/Ventana.java"
        orig = (fixtures.ROOT / "SwingApp" / path).read_text()
        with self.assertRaises(SecurityError):
            self.ed.apply("SwingApp", [{"op": "edit", "path": path, "old_string": 'setText("OK")',
                                         "new_string": 'setText("Aceptar")'}])
        with self.assertRaises(SecurityError):
            self.ed.apply("SwingApp", [{"op": "edit", "path": path, "old_string": "private JButton jButton1;",
                                         "new_string": "private JButton jButton1; private int x;"}])
        self.assertEqual((fixtures.ROOT / "SwingApp" / path).read_text(), orig)
        # El código de usuario fuera de las regiones generadas sí se puede editar
        self.ed.apply("SwingApp", [{"op": "edit", "path": path, "old_string": 'System.out.println("pulsado");',
                                     "new_string": 'System.out.println("pulsado!");'}])
        self.assertIn("pulsado!", (fixtures.ROOT / "SwingApp" / path).read_text())

    def test_form_es_solo_lectura(self):
        with self.assertRaises(SecurityError):
            self.ed.apply("SwingApp", [{"op": "edit", "path": "src/main/java/demo/Ventana.form",
                                         "old_string": "1.5", "new_string": "1.6"}])

    def test_lote_atomico_con_reversion(self):
        before = (fixtures.ROOT / "SimpleJava/src/Saludo.java").read_text()
        with self.assertRaises(AutopilotError):
            self.ed.apply("SimpleJava", [
                {"op": "edit", "path": "src/Saludo.java", "old_string": "nextInt(); int y", "new_string": "nextInt(); long y"},
                {"op": "edit", "path": "src/Saludo.java", "old_string": "NO_EXISTE", "new_string": "x"}])
        self.assertEqual((fixtures.ROOT / "SimpleJava/src/Saludo.java").read_text(), before)

    def test_deteccion_de_cambio_externo(self):
        r = self.ed.read("SimpleJava", "src/Saludo.java")
        p = fixtures.ROOT / "SimpleJava/src/Saludo.java"
        p.write_text(p.read_text() + "// editado en NetBeans\n", encoding="utf-8")
        with self.assertRaises(AutopilotError):
            self.ed.apply("SimpleJava", [{"op": "edit", "path": "src/Saludo.java", "old_string": "x + y",
                                           "new_string": "x - y", "expected_sha256": r["sha256"]}])

    def test_revertir(self):
        p = fixtures.ROOT / "HolaMaven/src/main/resources/nota.txt"
        r = self.ed.apply("HolaMaven", [{"op": "create", "path": "src/main/resources/nota.txt", "content": "uno"}])
        r2 = self.ed.apply("HolaMaven", [{"op": "edit", "path": "src/main/resources/nota.txt",
                                           "old_string": "uno", "new_string": "dos"}])
        self.assertEqual(p.read_text(), "dos")
        self.ed.revert("HolaMaven", batch=r2["batch"])
        self.assertEqual(p.read_text(), "uno")
        self.ed.revert("HolaMaven", batch=r["batch"])
        self.assertFalse(p.exists())
        s = self.ed.summary("HolaMaven")
        self.assertIn("scope", s)

    def test_proyectos_reales_intactos(self):
        self.assertEqual(manifest(REAL_ROOT), self.real_before)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Crea un entorno de pruebas aislado (carpeta raíz de mentira con proyectos de ejemplo)."""
import os
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SANDBOX = REPO / ".sandbox"
ROOT = SANDBOX / "projects"
DATA = SANDBOX / "data"
OUTSIDE = SANDBOX / "outside"          # fuera de la raíz: no debe ser accesible

POM = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
    <modelVersion>4.0.0</modelVersion>
    <groupId>demo</groupId><artifactId>{name}</artifactId><version>1.0</version><packaging>jar</packaging>
    <properties><maven.compiler.release>17</maven.compiler.release>
        <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding></properties>
</project>
"""

DESIGNER_JAVA = """package demo;

import javax.swing.JButton;
import javax.swing.JFrame;

public class Ventana extends JFrame {

    public Ventana() {
        initComponents();
    }

    // <editor-fold defaultstate="collapsed" desc="Generated Code">//GEN-BEGIN:initComponents
    private void initComponents() {
        jButton1 = new JButton();
        setDefaultCloseOperation(javax.swing.WindowConstants.EXIT_ON_CLOSE);
        jButton1.setText("OK");
        jButton1.addActionListener(new java.awt.event.ActionListener() {
            public void actionPerformed(java.awt.event.ActionEvent evt) {
                jButton1ActionPerformed(evt);
            }
        });
        getContentPane().add(jButton1);
        pack();
    }// </editor-fold>//GEN-END:initComponents

    private void jButton1ActionPerformed(java.awt.event.ActionEvent evt) {//GEN-FIRST:event_jButton1ActionPerformed
        System.out.println("pulsado");
    }//GEN-LAST:event_jButton1ActionPerformed

    // Variables declaration - do not modify//GEN-BEGIN:variables
    private JButton jButton1;
    // End of variables declaration//GEN-END:variables
}
"""


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def build_sandbox() -> dict:
    if SANDBOX.exists():
        # elimina uniones (junction) antes de borrar para no seguir enlaces
        for j in SANDBOX.rglob("*"):
            if j.is_junction() if hasattr(j, "is_junction") else False:
                os.rmdir(j)
        shutil.rmtree(SANDBOX, ignore_errors=False)
    ROOT.mkdir(parents=True)
    DATA.mkdir(parents=True)
    OUTSIDE.mkdir(parents=True)
    write(OUTSIDE / "secreto.txt", "NO DEBE LEERSE")

    # Proyecto Maven correcto
    write(ROOT / "HolaMaven" / "pom.xml", POM.format(name="HolaMaven"))
    write(ROOT / "HolaMaven" / "src/main/java/demo/Main.java",
          'package demo;\n\npublic class Main {\n    public static void main(String[] a) {\n'
          '        System.out.println("Hola Mundo");\n    }\n}\n')
    write(ROOT / "HolaMaven" / "src/main/resources/protegido.properties", "clave=valor\n")
    write(ROOT / "HolaMaven" / "src/main/resources/db.properties",
          "url=jdbc:mysql://localhost/test\nuser=root\npassword=SuperSecreta123\n")

    # Proyecto Maven con error de compilación conocido
    write(ROOT / "RotoMaven" / "pom.xml", POM.format(name="RotoMaven"))
    write(ROOT / "RotoMaven" / "src/main/java/demo/Main.java",
          'package demo;\n\npublic class Main {\n    public static void main(String[] a) {\n'
          '        int total = 5;\n        System.out.println(totl);\n    }\n}\n')

    # Proyecto sin sistema de construcción
    write(ROOT / "SimpleJava" / "src/Saludo.java",
          'public class Saludo {\n    public static void main(String[] a) {\n'
          '        java.util.Scanner s = new java.util.Scanner(System.in);\n'
          '        int x = s.nextInt(); int y = s.nextInt();\n        System.out.println(x + y);\n    }\n}\n')

    # Proyecto Swing con código del diseñador de NetBeans
    write(ROOT / "SwingApp" / "pom.xml", POM.format(name="SwingApp"))
    write(ROOT / "SwingApp" / "src/main/java/demo/Ventana.java", DESIGNER_JAVA)
    write(ROOT / "SwingApp" / "src/main/java/demo/Ventana.form",
          '<?xml version="1.0" encoding="UTF-8" ?>\n<Form version="1.5"><Properties/></Form>\n')
    write(ROOT / "SwingApp" / "src/main/java/demo/Main.java",
          'package demo;\n\npublic class Main {\n    public static void main(String[] a) {\n'
          '        System.out.println("swing listo");\n    }\n}\n')

    # Proyecto marcado como solo lectura en la configuración
    write(ROOT / "Intocable" / "pom.xml", POM.format(name="Intocable"))
    write(ROOT / "Intocable" / "src/main/java/demo/Main.java",
          'package demo;\n\npublic class Main {\n    public static void main(String[] a) {}\n}\n')

    # Unión (junction) que apunta fuera de la raíz: intento de escape
    junction = ROOT / "HolaMaven" / "escape"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(OUTSIDE)], capture_output=True, check=False)

    # Archivo suelto en la raíz (como los Ejercicio*.java del usuario)
    write(ROOT / "Ejercicio1.java", "public class Ejercicio1 {}\n")
    return {"root": ROOT, "data": DATA, "outside": OUTSIDE, "junction_created": junction.exists()}


def sandbox_config() -> dict:
    return {"root": str(ROOT), "data_dir": str(DATA),
            "protected_globs": ["HolaMaven/src/main/resources/protegido.properties"],
            "read_only_projects": ["Intocable"]}

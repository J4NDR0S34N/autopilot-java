package suma;

import java.math.BigDecimal;

/** Pruebas sin dependencias externas: sale con código 1 si alguna falla. */
public class CalculadoraTest {

    private static int fallos = 0;

    public static void main(String[] args) {
        caso("2", "3", "5");
        caso("-4", "-6", "-10");
        caso("-5", "5", "0");
        caso("0", "0", "0");
        caso("0", "7", "7");
        caso("1.5", "2.25", "3.75");
        caso("0.1", "0.2", "0.3");
        caso("2,5", "1,5", "4.0");
        caso("999999999999", "1", "1000000000000");
        try {
            Calculadora.parsear("abc");
            fallo("'abc' debía lanzar excepción");
        } catch (IllegalArgumentException e) {
            System.out.println("OK  parsear(\"abc\") lanza: " + e.getMessage());
        }
        try {
            Calculadora.parsear("");
            fallo("cadena vacía debía lanzar excepción");
        } catch (IllegalArgumentException e) {
            System.out.println("OK  parsear(\"\") lanza: " + e.getMessage());
        }
        System.out.println(fallos == 0 ? "TODAS LAS PRUEBAS PASARON" : fallos + " FALLO(S)");
        System.exit(fallos == 0 ? 0 : 1);
    }

    private static void caso(String a, String b, String esperado) {
        BigDecimal r = Calculadora.sumar(Calculadora.parsear(a), Calculadora.parsear(b));
        if (r.compareTo(new BigDecimal(esperado)) == 0) {
            System.out.println("OK  " + a + " + " + b + " = " + r.toPlainString());
        } else {
            fallo(a + " + " + b + " dio " + r + ", esperado " + esperado);
        }
    }

    private static void fallo(String msg) {
        fallos++;
        System.out.println("FAIL " + msg);
    }
}

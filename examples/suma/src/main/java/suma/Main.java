package suma;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.math.BigDecimal;

public class Main {

    public static void main(String[] args) throws IOException {
        BufferedReader in = new BufferedReader(new InputStreamReader(System.in));
        try {
            BigDecimal a = leer(in, "Primer número: ");
            BigDecimal b = leer(in, "Segundo número: ");
            System.out.println("Resultado: " + a.toPlainString() + " + " + b.toPlainString()
                    + " = " + Calculadora.sumar(a, b).toPlainString());
        } catch (IllegalArgumentException e) {
            System.err.println("Error: " + e.getMessage());
            System.exit(1);
        }
    }

    private static BigDecimal leer(BufferedReader in, String mensaje) throws IOException {
        System.out.print(mensaje);
        String linea = in.readLine();
        return Calculadora.parsear(linea);
    }
}

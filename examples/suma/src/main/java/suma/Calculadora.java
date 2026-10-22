package suma;

import java.math.BigDecimal;

/** Lógica de cálculo, independiente de la entrada/salida. */
public final class Calculadora {

    private Calculadora() {
    }

    public static BigDecimal sumar(BigDecimal a, BigDecimal b) {
        return a.add(b);
    }

    /** Convierte texto a número; acepta coma o punto decimal. */
    public static BigDecimal parsear(String texto) {
        if (texto == null || texto.isBlank()) {
            throw new IllegalArgumentException("Entrada vacía");
        }
        try {
            return new BigDecimal(texto.trim().replace(',', '.'));
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException("Número no válido: " + texto.trim());
        }
    }
}

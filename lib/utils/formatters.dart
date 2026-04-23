class Formatters {
  static String formatPower(double watts) {
    if (watts.abs() >= 1000) {
      return '${(watts / 1000).toStringAsFixed(1)} kW';
    }
    return '${watts.toStringAsFixed(0)} W';
  }

  static String formatEnergy(double wattHours) {
    if (wattHours.abs() >= 1000) {
      return '${(wattHours / 1000).toStringAsFixed(1)} kWh';
    }
    return '${wattHours.toStringAsFixed(0)} Wh';
  }

  /// Compact label for chart Y-axis (no units, shorter text to prevent overlap).
  static String formatAxisPower(double watts) {
    if (watts == 0) return '0';
    if (watts.abs() >= 1000) {
      final k = watts / 1000;
      return k == k.roundToDouble()
          ? '${k.toInt()}k'
          : '${k.toStringAsFixed(1)}k';
    }
    return watts.toStringAsFixed(0);
  }

  /// Compact label for chart Y-axis energy (no units).
  static String formatAxisEnergy(double wh) {
    if (wh == 0) return '0';
    if (wh.abs() >= 1000) {
      final k = wh / 1000;
      return k == k.roundToDouble()
          ? '${k.toInt()}k'
          : '${k.toStringAsFixed(1)}k';
    }
    return wh.toStringAsFixed(0);
  }
}

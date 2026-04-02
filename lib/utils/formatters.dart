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
}

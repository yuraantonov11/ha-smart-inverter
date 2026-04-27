import '../models/hems_optimization_profile.dart';

class TariffForecastService {
  TariffForecastData buildDayNightForecast({
    required double dayTariffUahPerKwh,
    required double nightTariffUahPerKwh,
    int dayStartHour = 7,
    int nightStartHour = 23,
    int horizonHours = 24,
  }) {
    final now = DateTime.now();
    final prices = <DateTime, double>{};

    for (var i = 0; i < horizonHours; i++) {
      final hour = now.add(Duration(hours: i));
      final h = hour.hour;
      final isNight = h >= nightStartHour || h < dayStartHour;
      prices[DateTime(hour.year, hour.month, hour.day, h)] =
          isNight ? nightTariffUahPerKwh : dayTariffUahPerKwh;
    }

    return TariffForecastData(
      pricePerKwh: prices,
      externalSource: 'local_day_night_profile',
    );
  }
}

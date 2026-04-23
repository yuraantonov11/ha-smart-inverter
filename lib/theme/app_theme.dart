import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

class AppTheme {
  // Semantic energy colors kept stable across widgets/charts.
  static const Color pvColor = Color(0xFFF6C453);
  static const Color gridColor = Color(0xFF38BDF8);
  static const Color batteryColor = Color(0xFF34D399);
  static const Color loadColor = Color(0xFFB794F6);
  static const Color pvGlowColor = Color(0xFFF9D26E);
  static const Color gridGlowColor = Color(0xFF7DD3FC);
  static const Color batteryGlowColor = Color(0xFF6EE7B7);
  static const Color loadGlowColor = Color(0xFFD8B4FE);

  static const Color _lightBg = Color(0xFFEEF3FA);
  static const Color _lightSurface = Color(0xFFF7FBFF);
  static const Color _lightMuted = Color(0xFFDCE6F2);
  static const Color _lightText = Color(0xFF112034);
  static const Color _lightTextSecondary = Color(0xFF2F4460);
  static const Color _lightBorder = Color(0xFFB8C8DA);

  static const Color _darkBg = Color(0xFF090F1D);
  static const Color _darkSurface = Color(0xFF121B2E);
  static const Color _darkMuted = Color(0xFF182235);
  static const Color _darkText = Color(0xFFE6EEFF);
  static const Color _darkTextSecondary = Color(0xFFA7B4CC);
  static const Color _darkBorder = Color(0xFF28364D);

  static const Color _primary = gridColor;
  static const Color _error = Color(0xFFEF4444);

  static bool _useCyrillicDisplay(String languageCode) {
    return languageCode.toLowerCase().startsWith('uk');
  }

  static TextStyle _displayStyle({
    required String languageCode,
    required Color color,
    required FontWeight weight,
    required double letterSpacing,
  }) {
    if (_useCyrillicDisplay(languageCode)) {
      // Orbitron has poor Cyrillic coverage; Exo 2 keeps a tech look for Ukrainian.
      return GoogleFonts.exo2(
        color: color,
        fontWeight: weight,
        letterSpacing: letterSpacing * 0.75,
      );
    }
    return GoogleFonts.orbitron(
      color: color,
      fontWeight: weight,
      letterSpacing: letterSpacing,
    );
  }

  static TextTheme _techTextTheme(
    Color body,
    Color secondary, {
    String languageCode = 'en',
  }) {
    final base = ThemeData.light().textTheme;
    return GoogleFonts.interTextTheme(base).copyWith(
      displayLarge: _displayStyle(
        languageCode: languageCode,
        color: body,
        weight: FontWeight.w700,
        letterSpacing: 1.2,
      ),
      displayMedium: _displayStyle(
        languageCode: languageCode,
        color: body,
        weight: FontWeight.w700,
        letterSpacing: 1.0,
      ),
      displaySmall: _displayStyle(
        languageCode: languageCode,
        color: body,
        weight: FontWeight.w700,
        letterSpacing: 0.9,
      ),
      headlineMedium: _displayStyle(
        languageCode: languageCode,
        color: body,
        weight: FontWeight.w700,
        letterSpacing: 0.8,
      ),
      headlineSmall: _displayStyle(
        languageCode: languageCode,
        color: body,
        weight: FontWeight.w600,
        letterSpacing: 0.7,
      ),
      titleLarge: _displayStyle(
        languageCode: languageCode,
        color: body,
        weight: FontWeight.w600,
        letterSpacing: 0.6,
      ),
      titleMedium: GoogleFonts.inter(
        color: body,
        fontWeight: FontWeight.w600,
      ),
      titleSmall: GoogleFonts.inter(
        color: body,
        fontWeight: FontWeight.w600,
      ),
      bodyLarge: GoogleFonts.inter(color: body),
      bodyMedium: GoogleFonts.inter(color: secondary),
      bodySmall: GoogleFonts.inter(color: secondary, fontSize: 12),
      labelLarge: GoogleFonts.inter(color: body, fontWeight: FontWeight.w600),
      labelMedium:
          GoogleFonts.inter(color: secondary, fontWeight: FontWeight.w600),
      labelSmall:
          GoogleFonts.inter(color: secondary, fontWeight: FontWeight.w600),
    );
  }

  static ThemeData lightThemeForLanguage(String languageCode) {
    final scheme = ColorScheme.fromSeed(
      seedColor: _primary,
      brightness: Brightness.light,
      surface: _lightSurface,
      error: _error,
    );
    final textTheme = _techTextTheme(
      _lightText,
      _lightTextSecondary,
      languageCode: languageCode,
    );

    return ThemeData(
      useMaterial3: true,
      brightness: Brightness.light,
      colorScheme: scheme,
      scaffoldBackgroundColor: _lightBg,
      canvasColor: _lightBg,
      cardColor: _lightSurface,
      textTheme: textTheme,
      primaryTextTheme: textTheme,
      appBarTheme: AppBarTheme(
        backgroundColor: _lightSurface.withValues(alpha: 0.96),
        foregroundColor: _lightText,
        elevation: 0,
        centerTitle: true,
        scrolledUnderElevation: 1,
        surfaceTintColor: Colors.transparent,
        titleTextStyle: textTheme.titleLarge,
      ),
      cardTheme: CardThemeData(
        color: _lightSurface.withValues(alpha: 0.98),
        elevation: 0,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(18)),
        margin: EdgeInsets.zero,
      ),
      dialogTheme: DialogThemeData(
        backgroundColor: _lightSurface,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(20),
          side: const BorderSide(color: _lightBorder, width: 1),
        ),
        titleTextStyle: textTheme.titleLarge?.copyWith(fontSize: 20),
        contentTextStyle: textTheme.bodyMedium?.copyWith(height: 1.45),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: _lightSurface.withValues(alpha: 0.98),
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        labelStyle: textTheme.bodyMedium,
        hintStyle: textTheme.bodyMedium?.copyWith(
          color: _lightTextSecondary.withValues(alpha: 0.85),
        ),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: _lightBorder),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: _lightBorder),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide(color: scheme.primary, width: 2),
        ),
        errorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: _error),
        ),
      ),
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          backgroundColor: scheme.primary,
          foregroundColor: Colors.white,
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
          shape:
              RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
          elevation: 0,
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: _lightText,
          side: const BorderSide(color: _lightBorder, width: 1),
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
          shape:
              RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        ),
      ),
      listTileTheme: const ListTileThemeData(
        textColor: _lightText,
        iconColor: _lightTextSecondary,
      ),
      switchTheme: SwitchThemeData(
        trackColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return scheme.primary.withValues(alpha: 0.35);
          }
          return _lightMuted;
        }),
      ),
      dropdownMenuTheme: DropdownMenuThemeData(
        textStyle: textTheme.bodyMedium?.copyWith(color: _lightText),
      ),
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: _lightSurface.withValues(alpha: 0.99),
        indicatorColor: scheme.primary.withValues(alpha: 0.16),
        labelTextStyle: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return const TextStyle(
              color: _lightText,
              fontWeight: FontWeight.w600,
            );
          }
          return const TextStyle(color: _lightTextSecondary);
        }),
      ),
      dividerTheme: const DividerThemeData(
        color: _lightBorder,
        thickness: 1,
        space: 1,
      ),
      focusColor: scheme.primary.withValues(alpha: 0.12),
      hoverColor: scheme.primary.withValues(alpha: 0.08),
    );
  }

  static ThemeData darkThemeForLanguage(String languageCode) {
    final scheme = ColorScheme.fromSeed(
      seedColor: _primary,
      brightness: Brightness.dark,
      surface: _darkSurface,
      error: _error,
    );
    final textTheme = _techTextTheme(
      _darkText,
      _darkTextSecondary,
      languageCode: languageCode,
    );

    return ThemeData(
      useMaterial3: true,
      brightness: Brightness.dark,
      colorScheme: scheme,
      scaffoldBackgroundColor: _darkBg,
      canvasColor: _darkBg,
      cardColor: _darkSurface,
      textTheme: textTheme,
      primaryTextTheme: textTheme,
      appBarTheme: AppBarTheme(
        backgroundColor: _darkBg.withValues(alpha: 0.66),
        foregroundColor: _darkText,
        elevation: 0,
        centerTitle: true,
        scrolledUnderElevation: 0,
        surfaceTintColor: Colors.transparent,
        titleTextStyle: textTheme.titleLarge,
      ),
      cardTheme: CardThemeData(
        color: _darkSurface.withValues(alpha: 0.7),
        elevation: 0,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(18)),
        margin: EdgeInsets.zero,
      ),
      dialogTheme: DialogThemeData(
        backgroundColor: _darkSurface,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(20),
          side: const BorderSide(color: _darkBorder, width: 1),
        ),
        titleTextStyle: textTheme.titleLarge?.copyWith(fontSize: 20),
        contentTextStyle: textTheme.bodyMedium?.copyWith(height: 1.45),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: _darkSurface.withValues(alpha: 0.56),
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        labelStyle: textTheme.bodyMedium,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: _darkBorder),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: _darkBorder),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide(color: scheme.primary, width: 2),
        ),
        errorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: _error),
        ),
      ),
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          backgroundColor: scheme.primary,
          foregroundColor: Colors.black,
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
          shape:
              RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
          elevation: 0,
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: scheme.primary,
          side: const BorderSide(color: _darkBorder, width: 1),
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
          shape:
              RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        ),
      ),
      dividerTheme: const DividerThemeData(
        color: _darkBorder,
        thickness: 1,
        space: 1,
      ),
      switchTheme: SwitchThemeData(
        trackColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return scheme.primary.withValues(alpha: 0.4);
          }
          return _darkMuted;
        }),
      ),
      focusColor: scheme.primary.withValues(alpha: 0.12),
      hoverColor: scheme.primary.withValues(alpha: 0.08),
    );
  }

  static ThemeData get lightTheme => lightThemeForLanguage('en');
  static ThemeData get darkTheme => darkThemeForLanguage('en');

  static const double radiusSmall = 8.0;
  static const double radiusMedium = 12.0;
  static const double radiusLarge = 16.0;
  static const double radiusXL = 24.0;

  static const double spacingXS = 4.0;
  static const double spacingS = 8.0;
  static const double spacingM = 12.0;
  static const double spacingL = 16.0;
  static const double spacingXL = 20.0;
  static const double spacing2XL = 24.0;
}

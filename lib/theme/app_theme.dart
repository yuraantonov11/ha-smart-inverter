import 'package:flutter/material.dart';

class AppTheme {
  // ======================== КОЛЬОРОВА ПАЛІТРА ========================

  // Світла тема
  static const Color _lightBg = Color(0xFFFAFAFA);
  static const Color _lightCard = Color(0xFFFFFFFF);
  static const Color _lightText = Color(0xFF1F2937);
  static const Color _lightTextSecondary = Color(0xFF6B7280);
  static const Color _lightBorder = Color(0xFFE5E7EB);

  // Темна тема
  static const Color _darkBg = Color(0xFF0F172A);
  static const Color _darkCard = Color(0xFF1E293B);
  static const Color _darkText = Color(0xFFF1F5F9);
  static const Color _darkTextSecondary = Color(0xFF94A3B8);
  static const Color _darkBorder = Color(0xFF334155);

  // Акцентні кольори
  static const Color _primary = Color(0xFFF59E0B); // Золотавий (замість amber)
  static const Color _error = Color(0xFFEF4444); // Червонь

  // ======================== СВІТЛА ТЕМА ========================
  static ThemeData get lightTheme {
    return ThemeData(
      useMaterial3: true,
      brightness: Brightness.light,
      colorSchemeSeed: _primary,

      // Основні кольори
      scaffoldBackgroundColor: _lightBg,
      canvasColor: _lightBg,
      cardColor: _lightCard,

      // Text Themes
      textTheme: const TextTheme(
        displayLarge: TextStyle(color: _lightText, fontWeight: FontWeight.bold),
        displayMedium:
            TextStyle(color: _lightText, fontWeight: FontWeight.bold),
        displaySmall: TextStyle(color: _lightText, fontWeight: FontWeight.bold),
        headlineMedium:
            TextStyle(color: _lightText, fontWeight: FontWeight.bold),
        headlineSmall:
            TextStyle(color: _lightText, fontWeight: FontWeight.w600),
        titleLarge: TextStyle(color: _lightText, fontWeight: FontWeight.w600),
        titleMedium: TextStyle(color: _lightText, fontWeight: FontWeight.w500),
        bodyLarge: TextStyle(color: _lightText),
        bodyMedium: TextStyle(color: _lightTextSecondary),
        bodySmall: TextStyle(color: _lightTextSecondary, fontSize: 12),
      ),

      // AppBar
      appBarTheme: const AppBarTheme(
        backgroundColor: _lightCard,
        foregroundColor: _lightText,
        elevation: 0,
        centerTitle: true,
        scrolledUnderElevation: 1,
        surfaceTintColor: Colors.transparent,
      ),

      // Cards
      cardTheme: CardThemeData(
        color: _lightCard,
        elevation: 0,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        margin: EdgeInsets.zero,
      ),

      // Dialogs
      dialogTheme: DialogThemeData(
        backgroundColor: _lightCard,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(20),
          side: const BorderSide(color: _lightBorder, width: 1),
        ),
        titleTextStyle: const TextStyle(
          color: _lightText,
          fontWeight: FontWeight.w600,
          fontSize: 20,
        ),
        contentTextStyle: const TextStyle(
          color: _lightTextSecondary,
          fontSize: 14,
          height: 1.45,
        ),
      ),

      // Input Decoration
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: _lightBg,
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
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
          borderSide: const BorderSide(color: _primary, width: 2),
        ),
        errorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: _error),
        ),
      ),

      // Buttons
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          backgroundColor: _primary,
          foregroundColor: Colors.white,
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
          shape:
              RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
          elevation: 0,
        ),
      ),

      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: _primary,
          side: const BorderSide(color: _lightBorder, width: 1),
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
          shape:
              RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        ),
      ),

      // Divider
      dividerTheme: const DividerThemeData(
        color: _lightBorder,
        thickness: 1,
        space: 1,
      ),

      // Others
      focusColor: _primary.withValues(alpha: 0.1),
      hoverColor: _primary.withValues(alpha: 0.08),
    );
  }

  // ======================== ТЕМНА ТЕМА ========================
  static ThemeData get darkTheme {
    return ThemeData(
      useMaterial3: true,
      brightness: Brightness.dark,
      colorSchemeSeed: _primary,

      // Основні кольори
      scaffoldBackgroundColor: _darkBg,
      canvasColor: _darkBg,
      cardColor: _darkCard,

      // Text Themes
      textTheme: const TextTheme(
        displayLarge: TextStyle(color: _darkText, fontWeight: FontWeight.bold),
        displayMedium: TextStyle(color: _darkText, fontWeight: FontWeight.bold),
        displaySmall: TextStyle(color: _darkText, fontWeight: FontWeight.bold),
        headlineMedium:
            TextStyle(color: _darkText, fontWeight: FontWeight.bold),
        headlineSmall: TextStyle(color: _darkText, fontWeight: FontWeight.w600),
        titleLarge: TextStyle(color: _darkText, fontWeight: FontWeight.w600),
        titleMedium: TextStyle(color: _darkText, fontWeight: FontWeight.w500),
        bodyLarge: TextStyle(color: _darkText),
        bodyMedium: TextStyle(color: _darkTextSecondary),
        bodySmall: TextStyle(color: _darkTextSecondary, fontSize: 12),
      ),

      // AppBar
      appBarTheme: const AppBarTheme(
        backgroundColor: _darkCard,
        foregroundColor: _darkText,
        elevation: 0,
        centerTitle: true,
        scrolledUnderElevation: 1,
        surfaceTintColor: Colors.transparent,
      ),

      // Cards
      cardTheme: CardThemeData(
        color: _darkCard,
        elevation: 0,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        margin: EdgeInsets.zero,
      ),

      // Dialogs
      dialogTheme: DialogThemeData(
        backgroundColor: _darkCard,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(20),
          side: const BorderSide(color: _darkBorder, width: 1),
        ),
        titleTextStyle: const TextStyle(
          color: _darkText,
          fontWeight: FontWeight.w600,
          fontSize: 20,
        ),
        contentTextStyle: const TextStyle(
          color: _darkTextSecondary,
          fontSize: 14,
          height: 1.45,
        ),
      ),

      // Input Decoration
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: _darkBg,
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
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
          borderSide: const BorderSide(color: _primary, width: 2),
        ),
        errorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: _error),
        ),
      ),

      // Buttons
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          backgroundColor: _primary,
          foregroundColor: Colors.black,
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
          shape:
              RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
          elevation: 0,
        ),
      ),

      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: _primary,
          side: const BorderSide(color: _darkBorder, width: 1),
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
          shape:
              RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        ),
      ),

      // Divider
      dividerTheme: const DividerThemeData(
        color: _darkBorder,
        thickness: 1,
        space: 1,
      ),

      // Others
      focusColor: _primary.withValues(alpha: 0.1),
      hoverColor: _primary.withValues(alpha: 0.08),
    );
  }

  // ======================== ДОПОМІЖНІ КОНСТАНТИ ========================

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

import 'package:flutter/material.dart';

class AppMotionTokens extends ThemeExtension<AppMotionTokens> {
  final Duration quick;
  final Duration regular;
  final Duration emphasized;
  final Curve standardCurve;
  final Curve emphasizedCurve;

  const AppMotionTokens({
    required this.quick,
    required this.regular,
    required this.emphasized,
    required this.standardCurve,
    required this.emphasizedCurve,
  });

  static const fallback = AppMotionTokens(
    quick: Duration(milliseconds: 160),
    regular: Duration(milliseconds: 280),
    emphasized: Duration(milliseconds: 420),
    standardCurve: Curves.easeOutCubic,
    emphasizedCurve: Curves.easeInOutCubicEmphasized,
  );

  @override
  AppMotionTokens copyWith({
    Duration? quick,
    Duration? regular,
    Duration? emphasized,
    Curve? standardCurve,
    Curve? emphasizedCurve,
  }) {
    return AppMotionTokens(
      quick: quick ?? this.quick,
      regular: regular ?? this.regular,
      emphasized: emphasized ?? this.emphasized,
      standardCurve: standardCurve ?? this.standardCurve,
      emphasizedCurve: emphasizedCurve ?? this.emphasizedCurve,
    );
  }

  @override
  AppMotionTokens lerp(ThemeExtension<AppMotionTokens>? other, double t) {
    if (other is! AppMotionTokens) return this;
    return AppMotionTokens(
      quick: _lerpDuration(quick, other.quick, t),
      regular: _lerpDuration(regular, other.regular, t),
      emphasized: _lerpDuration(emphasized, other.emphasized, t),
      standardCurve: t < 0.5 ? standardCurve : other.standardCurve,
      emphasizedCurve: t < 0.5 ? emphasizedCurve : other.emphasizedCurve,
    );
  }

  Duration _lerpDuration(Duration a, Duration b, double t) {
    final micros =
        a.inMicroseconds + ((b.inMicroseconds - a.inMicroseconds) * t).round();
    return Duration(microseconds: micros);
  }
}

class AppExpressiveTokens extends ThemeExtension<AppExpressiveTokens> {
  final double cornerSmall;
  final double cornerMedium;
  final double cornerLarge;
  final double cornerXL;
  final double cardBorderOpacity;
  final double softShadowOpacity;
  final double shellBackdropOpacity;
  final double navigationIndicatorOpacity;

  const AppExpressiveTokens({
    required this.cornerSmall,
    required this.cornerMedium,
    required this.cornerLarge,
    required this.cornerXL,
    required this.cardBorderOpacity,
    required this.softShadowOpacity,
    required this.shellBackdropOpacity,
    required this.navigationIndicatorOpacity,
  });

  static const fallback = AppExpressiveTokens(
    cornerSmall: 14,
    cornerMedium: 20,
    cornerLarge: 28,
    cornerXL: 36,
    cardBorderOpacity: 0.56,
    softShadowOpacity: 0.18,
    shellBackdropOpacity: 0.72,
    navigationIndicatorOpacity: 0.18,
  );

  @override
  AppExpressiveTokens copyWith({
    double? cornerSmall,
    double? cornerMedium,
    double? cornerLarge,
    double? cornerXL,
    double? cardBorderOpacity,
    double? softShadowOpacity,
    double? shellBackdropOpacity,
    double? navigationIndicatorOpacity,
  }) {
    return AppExpressiveTokens(
      cornerSmall: cornerSmall ?? this.cornerSmall,
      cornerMedium: cornerMedium ?? this.cornerMedium,
      cornerLarge: cornerLarge ?? this.cornerLarge,
      cornerXL: cornerXL ?? this.cornerXL,
      cardBorderOpacity: cardBorderOpacity ?? this.cardBorderOpacity,
      softShadowOpacity: softShadowOpacity ?? this.softShadowOpacity,
      shellBackdropOpacity: shellBackdropOpacity ?? this.shellBackdropOpacity,
      navigationIndicatorOpacity:
          navigationIndicatorOpacity ?? this.navigationIndicatorOpacity,
    );
  }

  @override
  AppExpressiveTokens lerp(
      ThemeExtension<AppExpressiveTokens>? other, double t) {
    if (other is! AppExpressiveTokens) return this;
    return AppExpressiveTokens(
      cornerSmall: _lerp(cornerSmall, other.cornerSmall, t),
      cornerMedium: _lerp(cornerMedium, other.cornerMedium, t),
      cornerLarge: _lerp(cornerLarge, other.cornerLarge, t),
      cornerXL: _lerp(cornerXL, other.cornerXL, t),
      cardBorderOpacity: _lerp(cardBorderOpacity, other.cardBorderOpacity, t),
      softShadowOpacity: _lerp(softShadowOpacity, other.softShadowOpacity, t),
      shellBackdropOpacity:
          _lerp(shellBackdropOpacity, other.shellBackdropOpacity, t),
      navigationIndicatorOpacity: _lerp(
          navigationIndicatorOpacity, other.navigationIndicatorOpacity, t),
    );
  }

  double _lerp(double a, double b, double t) => a + (b - a) * t;
}

extension AppThemeContext on BuildContext {
  AppMotionTokens get motion =>
      Theme.of(this).extension<AppMotionTokens>() ?? AppMotionTokens.fallback;

  AppExpressiveTokens get expressive =>
      Theme.of(this).extension<AppExpressiveTokens>() ??
      AppExpressiveTokens.fallback;
}

class AppTheme {
  // Semantic energy colors kept stable across widgets/charts.
  static const Color pvColor = Color(0xFFD88A00);
  static const Color gridColor = Color(0xFF1A7FEA);
  static const Color batteryColor = Color(0xFF1E9B63);
  static const Color loadColor = Color(0xFF8A4DDE);
  static const Color pvGlowColor = Color(0xFFF2B544);
  static const Color gridGlowColor = Color(0xFF58A8FF);
  static const Color batteryGlowColor = Color(0xFF4BCB8D);
  static const Color loadGlowColor = Color(0xFFB486F2);

  static const Color _lightBg = Color(0xFFF4F8FC);
  static const Color _lightSurface = Color(0xFFFFFFFF);
  static const Color _lightMuted = Color(0xFFE8EEF5);
  static const Color _lightText = Color(0xFF151F2D);
  static const Color _lightTextSecondary = Color(0xFF4E5D72);
  static const Color _lightBorder = Color(0xFFD0D9E6);

  static const Color _darkBg = Color(0xFF090F1D);
  static const Color _darkSurface = Color(0xFF121B2E);
  static const Color _darkMuted = Color(0xFF182235);
  static const Color _darkBorder = Color(0xFF28364D);

  static const Color _primary = Color(0xFF0B8FA4);
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
    final displayFamily =
        _useCyrillicDisplay(languageCode) ? 'Exo 2' : 'Orbitron';
    final adjustedLetterSpacing = _useCyrillicDisplay(languageCode)
        ? letterSpacing * 0.75
        : letterSpacing;

    return TextStyle(
      color: color,
      fontWeight: weight,
      letterSpacing: adjustedLetterSpacing,
      fontFamily: displayFamily,
      fontFamilyFallback: const ['Inter', 'Manrope', 'Segoe UI', 'Roboto'],
    );
  }

  static TextStyle _uiStyle({
    required Color color,
    FontWeight weight = FontWeight.w400,
    double? letterSpacing,
    double? fontSize,
  }) {
    return TextStyle(
      color: color,
      fontWeight: weight,
      letterSpacing: letterSpacing,
      fontSize: fontSize,
      fontFamily: 'Inter',
      fontFamilyFallback: const ['Manrope', 'Segoe UI', 'Roboto'],
    );
  }

  static TextTheme _expressiveTextTheme(
    Color body,
    Color secondary, {
    String languageCode = 'en',
    Brightness brightness = Brightness.light,
  }) {
    final base = ThemeData(
      useMaterial3: true,
      brightness: brightness,
    ).textTheme;
    final bodyTheme = base.apply(
      bodyColor: secondary,
      displayColor: body,
      fontFamily: 'Manrope',
    );

    return bodyTheme.copyWith(
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
      titleMedium: _uiStyle(
        color: body,
        weight: FontWeight.w600,
      ),
      titleSmall: _uiStyle(
        color: body,
        weight: FontWeight.w600,
      ),
      bodyLarge: _uiStyle(color: body),
      bodyMedium: _uiStyle(color: secondary),
      bodySmall: _uiStyle(color: secondary, fontSize: 12),
      labelLarge: _uiStyle(color: body, weight: FontWeight.w600),
      labelMedium: _uiStyle(
        color: secondary,
        weight: FontWeight.w500,
        letterSpacing: 0.5,
      ),
      labelSmall: _uiStyle(
        color: secondary,
        weight: FontWeight.w500,
        letterSpacing: 0.5,
      ),
    );
  }

  static ThemeData lightThemeForLanguage(String languageCode) {
    final scheme = ColorScheme.fromSeed(
      seedColor: _primary,
      brightness: Brightness.light,
      surface: _lightSurface,
      error: _error,
    );
    final textTheme = _expressiveTextTheme(
      scheme.onSurface,
      scheme.onSurfaceVariant,
      languageCode: languageCode,
      brightness: Brightness.light,
    );

    return ThemeData(
      useMaterial3: true,
      brightness: Brightness.light,
      colorScheme: scheme,
      typography: Typography.material2021(),
      scaffoldBackgroundColor:
          Color.alphaBlend(_lightBg.withValues(alpha: 0.82), scheme.surface),
      textTheme: textTheme,
      primaryTextTheme: textTheme,
      extensions: const [
        AppMotionTokens.fallback,
        AppExpressiveTokens.fallback,
      ],
      appBarTheme: AppBarTheme(
        backgroundColor: scheme.surface,
        foregroundColor: scheme.onSurface,
        elevation: 0,
        centerTitle: true,
        scrolledUnderElevation: 3,
        surfaceTintColor: scheme.surfaceTint,
        titleTextStyle: textTheme.titleLarge,
      ),
      cardTheme: CardThemeData(
        color: scheme.surfaceContainerLow,
        elevation: 1,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(radiusXL),
        ),
        margin: EdgeInsets.zero,
      ),
      dialogTheme: DialogThemeData(
        backgroundColor: scheme.surfaceContainerHigh,
        surfaceTintColor: scheme.surfaceTint,
        elevation: 1,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(radiusLarge),
          side: BorderSide(color: scheme.outlineVariant),
        ),
        titleTextStyle: textTheme.titleLarge?.copyWith(fontSize: 20),
        contentTextStyle: textTheme.bodyMedium?.copyWith(height: 1.45),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: scheme.surfaceContainer.withValues(alpha: 0.4),
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        labelStyle: textTheme.bodyMedium?.copyWith(color: scheme.primary),
        hintStyle: textTheme.bodyMedium?.copyWith(
          color: scheme.onSurfaceVariant.withValues(alpha: 0.8),
        ),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(radiusSmall),
          borderSide: BorderSide(color: scheme.outline),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(radiusSmall),
          borderSide: BorderSide(color: scheme.outline.withValues(alpha: 0.5)),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(radiusSmall),
          borderSide: BorderSide(color: scheme.primary, width: 2),
        ),
        errorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(radiusSmall),
          borderSide: const BorderSide(color: _error),
        ),
        focusedErrorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(radiusSmall),
          borderSide: const BorderSide(color: _error, width: 2),
        ),
        disabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(radiusSmall),
          borderSide: BorderSide(color: scheme.outline.withValues(alpha: 0.3)),
        ),
      ),
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          backgroundColor: scheme.primary,
          foregroundColor: scheme.onPrimary,
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(radiusMedium),
          ),
          elevation: 0,
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          backgroundColor: scheme.secondaryContainer,
          foregroundColor: scheme.onSecondaryContainer,
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(radiusMedium),
          ),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: scheme.onSurface,
          side: BorderSide(color: scheme.outline, width: 1),
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(radiusMedium),
          ),
        ),
      ),
      iconButtonTheme: IconButtonThemeData(
        style: IconButton.styleFrom(
          foregroundColor: scheme.primary,
          backgroundColor: scheme.primaryContainer.withValues(alpha: 0.12),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(radiusSmall),
          ),
        ),
      ),
      chipTheme: ChipThemeData(
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(radiusMedium),
        ),
        side: BorderSide(color: scheme.outline.withValues(alpha: 0.5)),
        selectedColor: scheme.secondaryContainer,
        backgroundColor: scheme.surfaceContainer.withValues(alpha: 0.6),
        labelStyle: textTheme.labelMedium ?? const TextStyle(),
      ),
      listTileTheme: const ListTileThemeData(
        textColor: _lightText,
        iconColor: _lightTextSecondary,
      ),
      switchTheme: SwitchThemeData(
        thumbColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return scheme.primary;
          }
          return scheme.outline;
        }),
        trackColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return scheme.primaryContainer;
          }
          return Color.alphaBlend(
            _lightMuted.withValues(alpha: 0.5),
            scheme.surfaceContainerHighest,
          );
        }),
        trackOutlineColor: WidgetStatePropertyAll(scheme.outline),
      ),
      dropdownMenuTheme: DropdownMenuThemeData(
        textStyle: textTheme.bodyMedium?.copyWith(color: scheme.onSurface),
        inputDecorationTheme: InputDecorationTheme(
          filled: true,
          fillColor: scheme.surfaceContainer.withValues(alpha: 0.4),
          contentPadding: const EdgeInsets.symmetric(horizontal: 12),
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(radiusSmall),
            borderSide: BorderSide(color: scheme.outline),
          ),
        ),
      ),
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: Colors.transparent,
        indicatorColor: scheme.secondaryContainer,
        elevation: 0,
        labelBehavior: NavigationDestinationLabelBehavior.alwaysShow,
        labelTextStyle: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return textTheme.labelSmall?.copyWith(
              color: scheme.primary,
              fontWeight: FontWeight.w700,
            );
          }
          return textTheme.labelSmall?.copyWith(color: scheme.onSurfaceVariant);
        }),
      ),
      navigationRailTheme: NavigationRailThemeData(
        backgroundColor: Colors.transparent,
        useIndicator: true,
        indicatorColor: scheme.secondaryContainer,
        selectedIconTheme: IconThemeData(color: scheme.primary, size: 24),
        unselectedIconTheme: IconThemeData(color: scheme.onSurfaceVariant),
        selectedLabelTextStyle: textTheme.labelSmall?.copyWith(
          color: scheme.primary,
          fontWeight: FontWeight.w700,
        ),
        unselectedLabelTextStyle: textTheme.labelSmall?.copyWith(
          color: scheme.onSurfaceVariant,
        ),
      ),
      dividerTheme: DividerThemeData(
        color: Color.alphaBlend(
          _lightBorder.withValues(alpha: 0.35),
          scheme.outlineVariant,
        ),
        thickness: 1,
        space: 1,
      ),
      floatingActionButtonTheme: FloatingActionButtonThemeData(
        backgroundColor: scheme.primaryContainer,
        foregroundColor: scheme.onPrimaryContainer,
      ),
      bottomSheetTheme: BottomSheetThemeData(
        backgroundColor: scheme.surfaceContainerLow,
        surfaceTintColor: scheme.surfaceTint,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(radiusXL)),
        ),
        showDragHandle: true,
      ),
      snackBarTheme: SnackBarThemeData(
        backgroundColor: scheme.inverseSurface,
        contentTextStyle: textTheme.bodyMedium?.copyWith(
          color: scheme.onInverseSurface,
        ),
        actionTextColor: scheme.inversePrimary,
        behavior: SnackBarBehavior.floating,
      ),
      navigationDrawerTheme: NavigationDrawerThemeData(
        backgroundColor: scheme.surface,
        indicatorColor: scheme.secondaryContainer,
      ),
      focusColor: scheme.primary.withValues(alpha: 0.12),
      hoverColor: scheme.primary.withValues(alpha: 0.08),
      tooltipTheme: TooltipThemeData(
        decoration: BoxDecoration(
          color: scheme.inverseSurface,
          borderRadius: BorderRadius.circular(radiusSmall),
        ),
        textStyle: textTheme.bodySmall?.copyWith(
          color: scheme.onInverseSurface,
          fontWeight: FontWeight.w500,
        ),
        margin: const EdgeInsets.symmetric(horizontal: 12),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      ),
    );
  }

  static ThemeData darkThemeForLanguage(String languageCode) {
    final scheme = ColorScheme.fromSeed(
      seedColor: _primary,
      brightness: Brightness.dark,
      surface: _darkSurface,
      error: _error,
    );
    final textTheme = _expressiveTextTheme(
      scheme.onSurface,
      scheme.onSurfaceVariant,
      languageCode: languageCode,
      brightness: Brightness.dark,
    );

    return ThemeData(
      useMaterial3: true,
      brightness: Brightness.dark,
      colorScheme: scheme,
      typography: Typography.material2021(),
      applyElevationOverlayColor: true,
      scaffoldBackgroundColor:
          Color.alphaBlend(_darkBg.withValues(alpha: 0.82), scheme.surface),
      textTheme: textTheme,
      primaryTextTheme: textTheme,
      extensions: const [
        AppMotionTokens.fallback,
        AppExpressiveTokens.fallback,
      ],
      appBarTheme: AppBarTheme(
        backgroundColor: scheme.surface,
        foregroundColor: scheme.onSurface,
        elevation: 0,
        centerTitle: true,
        scrolledUnderElevation: 3,
        surfaceTintColor: scheme.surfaceTint,
        titleTextStyle: textTheme.titleLarge,
      ),
      cardTheme: CardThemeData(
        color: scheme.surfaceContainerLow,
        elevation: 1,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(radiusXL),
        ),
        margin: EdgeInsets.zero,
      ),
      dialogTheme: DialogThemeData(
        backgroundColor: scheme.surfaceContainerHigh,
        surfaceTintColor: scheme.surfaceTint,
        elevation: 1,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(radiusLarge),
          side: BorderSide(color: scheme.outlineVariant),
        ),
        titleTextStyle: textTheme.titleLarge?.copyWith(fontSize: 20),
        contentTextStyle: textTheme.bodyMedium?.copyWith(height: 1.45),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: scheme.surfaceContainer.withValues(alpha: 0.5),
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        labelStyle: textTheme.bodyMedium?.copyWith(color: scheme.primary),
        hintStyle: textTheme.bodyMedium?.copyWith(
          color: scheme.onSurfaceVariant.withValues(alpha: 0.8),
        ),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(radiusSmall),
          borderSide: BorderSide(color: scheme.outline),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(radiusSmall),
          borderSide: BorderSide(color: scheme.outline.withValues(alpha: 0.5)),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(radiusSmall),
          borderSide: BorderSide(color: scheme.primary, width: 2),
        ),
        errorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(radiusSmall),
          borderSide: const BorderSide(color: _error),
        ),
        focusedErrorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(radiusSmall),
          borderSide: const BorderSide(color: _error, width: 2),
        ),
        disabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(radiusSmall),
          borderSide: BorderSide(color: scheme.outline.withValues(alpha: 0.3)),
        ),
      ),
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          backgroundColor: scheme.primary,
          foregroundColor: scheme.onPrimary,
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(radiusMedium),
          ),
          elevation: 0,
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          backgroundColor: scheme.secondaryContainer,
          foregroundColor: scheme.onSecondaryContainer,
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(radiusMedium),
          ),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: scheme.primary,
          side: BorderSide(color: scheme.outline, width: 1),
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(radiusMedium),
          ),
        ),
      ),
      iconButtonTheme: IconButtonThemeData(
        style: IconButton.styleFrom(
          foregroundColor: scheme.primary,
          backgroundColor: scheme.primaryContainer.withValues(alpha: 0.12),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(radiusSmall),
          ),
        ),
      ),
      chipTheme: ChipThemeData(
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(radiusMedium),
        ),
        side: BorderSide(color: scheme.outline.withValues(alpha: 0.5)),
        selectedColor: scheme.secondaryContainer.withValues(alpha: 0.9),
        backgroundColor: scheme.surfaceContainer.withValues(alpha: 0.7),
        labelStyle: textTheme.labelMedium ?? const TextStyle(),
      ),
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: Colors.transparent,
        indicatorColor: scheme.secondaryContainer.withValues(alpha: 0.8),
        elevation: 0,
        labelBehavior: NavigationDestinationLabelBehavior.alwaysShow,
        labelTextStyle: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return textTheme.labelSmall?.copyWith(
              color: scheme.primary,
              fontWeight: FontWeight.w700,
            );
          }
          return textTheme.labelSmall?.copyWith(color: scheme.onSurfaceVariant);
        }),
      ),
      navigationRailTheme: NavigationRailThemeData(
        backgroundColor: Colors.transparent,
        useIndicator: true,
        indicatorColor: scheme.secondaryContainer.withValues(alpha: 0.8),
        selectedIconTheme: IconThemeData(color: scheme.primary, size: 24),
        unselectedIconTheme: IconThemeData(color: scheme.onSurfaceVariant),
        selectedLabelTextStyle: textTheme.labelSmall?.copyWith(
          color: scheme.primary,
          fontWeight: FontWeight.w700,
        ),
        unselectedLabelTextStyle: textTheme.labelSmall?.copyWith(
          color: scheme.onSurfaceVariant,
        ),
      ),
      dividerTheme: DividerThemeData(
        color: Color.alphaBlend(
          _darkBorder.withValues(alpha: 0.45),
          scheme.outlineVariant,
        ),
        thickness: 1,
        space: 1,
      ),
      switchTheme: SwitchThemeData(
        thumbColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return scheme.primary;
          }
          return scheme.outline;
        }),
        trackColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return scheme.primaryContainer;
          }
          return Color.alphaBlend(
            _darkMuted.withValues(alpha: 0.45),
            scheme.surfaceContainerHighest,
          );
        }),
        trackOutlineColor: WidgetStatePropertyAll(scheme.outline),
      ),
      dropdownMenuTheme: DropdownMenuThemeData(
        textStyle: textTheme.bodyMedium?.copyWith(color: scheme.onSurface),
        inputDecorationTheme: InputDecorationTheme(
          filled: true,
          fillColor: scheme.surfaceContainer.withValues(alpha: 0.5),
          contentPadding: const EdgeInsets.symmetric(horizontal: 12),
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(radiusSmall),
            borderSide: BorderSide(color: scheme.outline),
          ),
        ),
      ),
      floatingActionButtonTheme: FloatingActionButtonThemeData(
        backgroundColor: scheme.primaryContainer,
        foregroundColor: scheme.onPrimaryContainer,
      ),
      bottomSheetTheme: BottomSheetThemeData(
        backgroundColor: scheme.surfaceContainerLow,
        surfaceTintColor: scheme.surfaceTint,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(radiusXL)),
        ),
        showDragHandle: true,
      ),
      snackBarTheme: SnackBarThemeData(
        backgroundColor: scheme.inverseSurface,
        contentTextStyle: textTheme.bodyMedium?.copyWith(
          color: scheme.onInverseSurface,
        ),
        actionTextColor: scheme.inversePrimary,
        behavior: SnackBarBehavior.floating,
      ),
      navigationDrawerTheme: NavigationDrawerThemeData(
        backgroundColor: scheme.surface,
        indicatorColor: scheme.secondaryContainer,
      ),
      focusColor: scheme.primary.withValues(alpha: 0.12),
      hoverColor: scheme.primary.withValues(alpha: 0.08),
      tooltipTheme: TooltipThemeData(
        decoration: BoxDecoration(
          color: scheme.inverseSurface,
          borderRadius: BorderRadius.circular(radiusSmall),
        ),
        textStyle: textTheme.bodySmall?.copyWith(
          color: scheme.onInverseSurface,
          fontWeight: FontWeight.w500,
        ),
        margin: const EdgeInsets.symmetric(horizontal: 12),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      ),
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

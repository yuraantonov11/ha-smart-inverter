import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';
import 'dart:async';
import '../l10n/app_localizations.dart';
import '../providers/app_provider.dart';
import '../theme/app_theme.dart';
import '../widgets/app_components.dart';

class AuthScreen extends StatefulWidget {
  const AuthScreen({super.key});

  @override
  State<AuthScreen> createState() => _AuthScreenState();
}

class _AuthScreenState extends State<AuthScreen> {
  final _emailController = TextEditingController();
  final _passwordController = TextEditingController();
  final _emailFocus = FocusNode();
  final _passwordFocus = FocusNode();
  bool _isLoading = false;
  bool _obscurePassword = true;
  Timer? _clipboardWatchTimer;
  String? _lastClipboardSnapshot;

  @override
  void dispose() {
    _clipboardWatchTimer?.cancel();
    _emailFocus.dispose();
    _passwordFocus.dispose();
    _emailController.dispose();
    _passwordController.dispose();
    super.dispose();
  }

  Future<void> _watchClipboardForWinVPaste(
      TextEditingController controller) async {
    _clipboardWatchTimer?.cancel();

    // Зберігаємо поточний стан буфера обміну
    try {
      final initialData = await Clipboard.getData(Clipboard.kTextPlain);
      _lastClipboardSnapshot = initialData?.text;
    } catch (_) {
      _lastClipboardSnapshot = null;
    }

    var ticks = 0;
    const maxTicks = 30; // Перевіряємо до 1.5 секунди
    const intervalMs = 50; // Більш частої перевірки для Win+V

    _clipboardWatchTimer =
        Timer.periodic(Duration(milliseconds: intervalMs), (timer) async {
      ticks++;
      if (ticks > maxTicks) {
        timer.cancel();
        return;
      }

      try {
        final data = await Clipboard.getData(Clipboard.kTextPlain);
        final text = data?.text;

        // Перевіряємо, чи змінився вміст буфера
        if (text != null &&
            text.isNotEmpty &&
            text != _lastClipboardSnapshot &&
            text.trim().isNotEmpty) {
          _lastClipboardSnapshot = text;
          _insertAtCursor(controller, text);
          timer.cancel();
          return;
        }
      } catch (_) {
        // Ігноруємо помилки при читанні буфера
      }
    });
  }

  void _insertAtCursor(TextEditingController controller, String text) {
    final selection = controller.selection;
    final currentText = controller.text;

    if (!selection.isValid) {
      // Додаємо текст в кінець
      final newText = currentText + text;
      controller.text = newText;
      controller.selection = TextSelection.collapsed(offset: newText.length);
      return;
    }

    // Замінюємо вибраний текст
    final start = selection.start;
    final end = selection.end;
    final updated = currentText.replaceRange(start, end, text);

    controller.value = controller.value.copyWith(
      text: updated,
      selection: TextSelection.collapsed(offset: start + text.length),
      composing: TextRange.empty,
    );
  }

  KeyEventResult _handleWinVKey(
      KeyEvent event, TextEditingController controller) {
    // Обробляємо Win+V на Windows
    if (event is KeyDownEvent) {
      final isWinKey = HardwareKeyboard.instance.isMetaPressed;
      final isVKey = event.logicalKey == LogicalKeyboardKey.keyV;

      if (isWinKey && isVKey) {
        // Win+V натиснута - починаємо стежити за змінами буфера
        _watchClipboardForWinVPaste(controller);
        // Повертаємо handled, щоб запобігти стандартній обробці
        return KeyEventResult.handled;
      }
    }

    return KeyEventResult.ignored;
  }

  void _handleLogin() async {
    if (_emailController.text.isEmpty || _passwordController.text.isEmpty) {
      return;
    }

    setState(() => _isLoading = true);

    final provider = context.read<AppStateProvider>();
    final l10n = AppLocalizations.of(context)!;
    var success = await provider.login(
      _emailController.text.trim(),
      _passwordController.text.trim(),
    );

    if (!mounted) return;
    setState(() => _isLoading = false);

    if (!success) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(l10n.loginFailed),
          backgroundColor: Colors.redAccent,
        ),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    final l10n = AppLocalizations.of(context)!;

    return Scaffold(
      body: Stack(
        children: [
          Positioned.fill(
            child: DecoratedBox(
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                  colors: [
                    theme.colorScheme.primary
                        .withValues(alpha: isDark ? 0.10 : 0.08),
                    theme.scaffoldBackgroundColor,
                    AppTheme.loadColor.withValues(alpha: isDark ? 0.10 : 0.06),
                  ],
                ),
              ),
            ),
          ),
          Center(
            child: SingleChildScrollView(
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 430),
                child: AppGlassSurface(
                  isStrong: true,
                  borderRadius: AppTheme.radiusXL,
                  child: Padding(
                    padding: const EdgeInsets.all(32),
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Container(
                          width: 74,
                          height: 74,
                          decoration: BoxDecoration(
                            shape: BoxShape.circle,
                            color: AppTheme.pvColor.withValues(alpha: 0.16),
                            border: Border.all(
                                color: AppTheme.pvColor.withValues(alpha: 0.5)),
                            boxShadow: [
                              BoxShadow(
                                color: AppTheme.pvColor.withValues(alpha: 0.32),
                                blurRadius: 22,
                              ),
                            ],
                          ),
                          child: const Icon(
                            Icons.solar_power_rounded,
                            size: 40,
                            color: AppTheme.pvColor,
                          ),
                        ),
                        const SizedBox(height: 16),
                        Text(
                          l10n.appTitle,
                          style: theme.textTheme.headlineMedium?.copyWith(
                            fontWeight: FontWeight.w700,
                            letterSpacing: 1.1,
                          ),
                        ),
                        const SizedBox(height: 8),
                        Text(
                          l10n.signInCloud,
                          style: theme.textTheme.bodyMedium,
                          textAlign: TextAlign.center,
                        ),
                        const SizedBox(height: 32),
                        Focus(
                          focusNode: _emailFocus,
                          onKeyEvent: (_, event) =>
                              _handleWinVKey(event, _emailController),
                          child: TextField(
                            controller: _emailController,
                            enableInteractiveSelection: true,
                            decoration: InputDecoration(
                              labelText: l10n.email,
                              prefixIcon: const Icon(Icons.email_outlined),
                              border: OutlineInputBorder(
                                  borderRadius: BorderRadius.circular(14)),
                            ),
                          ),
                        ),
                        const SizedBox(height: 16),
                        Focus(
                          focusNode: _passwordFocus,
                          onKeyEvent: (_, event) =>
                              _handleWinVKey(event, _passwordController),
                          child: TextField(
                            controller: _passwordController,
                            obscureText: _obscurePassword,
                            enableInteractiveSelection: true,
                            decoration: InputDecoration(
                              labelText: l10n.password,
                              prefixIcon: const Icon(Icons.lock_outline),
                              suffixIcon: IconButton(
                                icon: Icon(_obscurePassword
                                    ? Icons.visibility_off
                                    : Icons.visibility),
                                onPressed: () => setState(
                                    () => _obscurePassword = !_obscurePassword),
                              ),
                              border: OutlineInputBorder(
                                  borderRadius: BorderRadius.circular(14)),
                            ),
                            onSubmitted: (_) => _handleLogin(),
                          ),
                        ),
                        const SizedBox(height: 32),
                        SizedBox(
                          width: double.infinity,
                          height: 50,
                          child: ElevatedButton(
                            onPressed: _isLoading ? null : _handleLogin,
                            style: ElevatedButton.styleFrom(
                              backgroundColor: theme.colorScheme.primary,
                              foregroundColor: theme.colorScheme.onPrimary,
                              shape: RoundedRectangleBorder(
                                  borderRadius: BorderRadius.circular(14)),
                            ),
                            child: _isLoading
                                ? SizedBox(
                                    width: 24,
                                    height: 24,
                                    child: CircularProgressIndicator(
                                        color: theme.colorScheme.onPrimary,
                                        strokeWidth: 2),
                                  )
                                : Text(
                                    l10n.login,
                                    style: theme.textTheme.titleMedium
                                        ?.copyWith(fontWeight: FontWeight.w700),
                                  ),
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

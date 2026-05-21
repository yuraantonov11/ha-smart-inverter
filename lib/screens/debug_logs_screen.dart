import 'package:flutter/material.dart';
import '../services/log_service.dart';
import 'dart:io';

class DebugLogsScreen extends StatefulWidget {
  const DebugLogsScreen({super.key});

  @override
  State<DebugLogsScreen> createState() => _DebugLogsScreenState();
}

class _DebugLogsScreenState extends State<DebugLogsScreen> {
  String _logContent = 'Loading...';
  bool _isLoading = true;
  String? _logFilePath;
  List<FileSystemEntity> _logFiles = [];

  @override
  void initState() {
    super.initState();
    _loadLogs();
  }

  Future<void> _loadLogs() async {
    setState(() => _isLoading = true);
    try {
      final content = await LogService.readCriticalLog();
      final filePath = await LogService.getDebugLogPath();
      final files = await LogService.listDebugLogFiles();

      setState(() {
        _logContent = content;
        _logFilePath = filePath;
        _logFiles = files;
        _isLoading = false;
      });
    } catch (e) {
      setState(() {
        _logContent = 'Error loading logs: $e';
        _isLoading = false;
      });
    }
  }

  Future<void> _clearLogs() async {
    final confirm = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Clear Debug Logs'),
        content: const Text(
            'This will delete all debug log files. This action cannot be undone.'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child:
                const Text('Delete All', style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );

    if (confirm == true) {
      await LogService.clearDebugLogs();
      await _loadLogs();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Debug logs cleared')),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    return Scaffold(
      appBar: AppBar(
        title: const Text('🔍 Debug Logs (HEMS Events)'),
        backgroundColor: theme.colorScheme.primary,
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            tooltip: 'Reload logs',
            onPressed: _loadLogs,
          ),
          IconButton(
            icon: const Icon(Icons.delete_forever),
            tooltip: 'Clear all logs',
            onPressed: _clearLogs,
          ),
        ],
      ),
      body: _isLoading
          ? const Center(child: CircularProgressIndicator())
          : Column(
              children: [
                // Info section
                Padding(
                  padding: const EdgeInsets.all(12),
                  child: Container(
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: isDark ? Colors.grey[900] : Colors.blue[50],
                      borderRadius: BorderRadius.circular(8),
                      border: Border.all(
                        color: theme.colorScheme.primary.withValues(alpha: 0.3),
                      ),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          '📁 Log File Information',
                          style: theme.textTheme.titleMedium?.copyWith(
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                        const SizedBox(height: 8),
                        if (_logFilePath != null) ...[
                          Text(
                            'Path: $_logFilePath',
                            style: theme.textTheme.bodySmall,
                            overflow: TextOverflow.ellipsis,
                          ),
                          const SizedBox(height: 6),
                        ],
                        Text(
                          'Total log files: ${_logFiles.length}',
                          style: theme.textTheme.bodySmall,
                        ),
                        const SizedBox(height: 8),
                        Text(
                          'Categories being logged:',
                          style: theme.textTheme.labelSmall,
                        ),
                        const SizedBox(height: 4),
                        Wrap(
                          spacing: 6,
                          runSpacing: 4,
                          children: [
                            _LogCategory('BATTERY_SAFETY', Colors.red),
                            _LogCategory('BATTERY_RECOVERY', Colors.orange),
                            _LogCategory('EVENING_PROTECTION', Colors.amber),
                            _LogCategory('MODE_CONFLICT', Colors.purple),
                            _LogCategory('STALE_DATA', Colors.red),
                            _LogCategory('EMERGENCY_CHARGE', Colors.red),
                          ],
                        ),
                      ],
                    ),
                  ),
                ),
                // Log content
                Expanded(
                  child: Container(
                    margin: const EdgeInsets.all(12),
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: isDark ? Colors.grey[900] : Colors.grey[100],
                      borderRadius: BorderRadius.circular(8),
                      border: Border.all(
                        color: theme.dividerColor,
                      ),
                    ),
                    child: SingleChildScrollView(
                      child: SelectableText(
                        _logContent.isEmpty ? 'No logs yet' : _logContent,
                        style: theme.textTheme.bodySmall?.copyWith(
                          fontFamily: 'Courier New',
                          height: 1.5,
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

class _LogCategory extends StatelessWidget {
  final String name;
  final Color color;

  const _LogCategory(this.name, this.color);

  @override
  Widget build(BuildContext context) {
    return Chip(
      label: Text(
        name,
        style: const TextStyle(fontSize: 11, color: Colors.white),
      ),
      backgroundColor: color,
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
    );
  }
}

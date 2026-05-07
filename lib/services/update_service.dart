import 'dart:convert';
import 'dart:io';
import 'package:http/http.dart' as http;
import 'package:open_file/open_file.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:path_provider/path_provider.dart';
import 'log_service.dart';

class UpdateInfo {
  final bool hasUpdate;
  final String currentVersion;
  final String latestVersion;
  final String? releaseName;
  final String? releaseNotes;
  final DateTime? publishedAt;
  final String? assetName;
  final String? downloadUrl;

  const UpdateInfo({
    required this.hasUpdate,
    required this.currentVersion,
    required this.latestVersion,
    this.releaseName,
    this.releaseNotes,
    this.publishedAt,
    this.assetName,
    this.downloadUrl,
  });
}

class UpdateService {
  static const String repoUrl =
      'https://api.github.com/repos/yuraantonov11/siseli-app/releases/latest';
  static final _client = http.Client();

  static List<String> get _supportedAssetExtensions =>
      Platform.isAndroid ? ['.apk'] : ['.exe', '.msi', '.msix'];

  static Map<String, String> get _headers => {
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'smart-inverter-app-updater',
      };

  static String _cleanVersion(String raw) {
    return raw.trim().startsWith('v') ? raw.trim().substring(1) : raw.trim();
  }

  static ({List<int> semantic, int build}) _parseVersion(String rawVersion) {
    final cleaned = _cleanVersion(rawVersion);
    final plusParts = cleaned.split('+');
    final semanticPart = plusParts.first;
    final semantic = semanticPart
        .split('.')
        .map((part) => int.tryParse(part) ?? 0)
        .toList(growable: true);
    final build = plusParts.length > 1 ? int.tryParse(plusParts[1]) ?? 0 : 0;
    return (semantic: semantic, build: build);
  }

  static String _twoDigits(int value) => value.toString().padLeft(2, '0');

  static String formatPublishedAt(DateTime? dt) {
    if (dt == null) return '--';
    final local = dt.toLocal();
    return '${local.year}-${_twoDigits(local.month)}-${_twoDigits(local.day)} ${_twoDigits(local.hour)}:${_twoDigits(local.minute)}';
  }

  static Future<UpdateInfo> fetchUpdateInfo() async {
    final packageInfo = await PackageInfo.fromPlatform();
    final buildNumber = int.tryParse(packageInfo.buildNumber) ?? 0;
    final currentVersion = '${_cleanVersion(packageInfo.version)}+$buildNumber';

    try {
      final response = await _client
          .get(Uri.parse(repoUrl), headers: _headers)
          .timeout(const Duration(seconds: 15));
      if (response.statusCode != 200) {
        LogService.log(
            '⚠️ update.check failed: status=${response.statusCode}, body=${response.body}');
        return UpdateInfo(
          hasUpdate: false,
          currentVersion: currentVersion,
          latestVersion: currentVersion,
        );
      }

      final data = json.decode(response.body) as Map<String, dynamic>;
      final latestTag = (data['tag_name'] as String?) ?? currentVersion;
      final latestVersion = _cleanVersion(latestTag);
      final hasUpdate = _isVersionNewer(latestVersion, currentVersion);

      final assets = (data['assets'] as List<dynamic>? ?? const [])
          .whereType<Map<String, dynamic>>()
          .toList();
      final asset = _pickBestInstallerAsset(assets);

      return UpdateInfo(
        hasUpdate: hasUpdate,
        currentVersion: currentVersion,
        latestVersion: latestVersion,
        releaseName: data['name']?.toString(),
        releaseNotes: data['body']?.toString(),
        publishedAt: DateTime.tryParse(data['published_at']?.toString() ?? ''),
        assetName: asset?['name']?.toString(),
        downloadUrl: asset?['browser_download_url']?.toString(),
      );
    } catch (e) {
      LogService.log('❌ update.check exception', error: e);
      return UpdateInfo(
        hasUpdate: false,
        currentVersion: currentVersion,
        latestVersion: currentVersion,
      );
    }
  }

  static Future<bool> checkForUpdate() async {
    final info = await fetchUpdateInfo();
    return info.hasUpdate;
  }

  static bool _isVersionNewer(String latest, String current) {
    final latestVersion = _parseVersion(latest);
    final currentVersion = _parseVersion(current);
    final latestParts = List<int>.from(latestVersion.semantic);
    final currentParts = List<int>.from(currentVersion.semantic);

    final maxLen = latestParts.length > currentParts.length
        ? latestParts.length
        : currentParts.length;
    while (latestParts.length < maxLen) {
      latestParts.add(0);
    }
    while (currentParts.length < maxLen) {
      currentParts.add(0);
    }

    for (var i = 0; i < maxLen; i++) {
      if (latestParts[i] > currentParts[i]) return true;
      if (latestParts[i] < currentParts[i]) return false;
    }
    return latestVersion.build > currentVersion.build;
  }

  static Map<String, dynamic>? _pickBestInstallerAsset(
      List<Map<String, dynamic>> assets) {
    final extensions = _supportedAssetExtensions;

    final supported = assets.where((asset) {
      final name = asset['name']?.toString().toLowerCase() ?? '';
      return extensions.any((ext) => name.endsWith(ext));
    }).toList();

    if (supported.isEmpty) return null;

    // Android: prefer arm64-v8a, then armeabi-v7a, then any APK
    if (Platform.isAndroid) {
      const abiPriority = ['arm64-v8a', 'armeabi-v7a', 'x86_64'];
      for (final abi in abiPriority) {
        final match = supported.cast<Map<String, dynamic>?>().firstWhere(
              (a) => (a?['name']?.toString() ?? '').contains(abi),
              orElse: () => null,
            );
        if (match != null) return match;
      }
      return supported.first;
    }

    // Windows: sort by extension priority (.exe > .msi > .msix)
    int extRank(String name) {
      final lower = name.toLowerCase();
      for (var i = 0; i < extensions.length; i++) {
        if (lower.endsWith(extensions[i])) return i;
      }
      return 999;
    }

    supported.sort((a, b) {
      return extRank(a['name'].toString()) - extRank(b['name'].toString());
    });
    return supported.first;
  }

  static Future<String?> downloadUpdate(
      [void Function(double progress)? onProgress]) async {
    final info = await fetchUpdateInfo();
    if (info.downloadUrl == null || info.assetName == null) {
      LogService.log(
          '⚠️ update.download skipped: no supported installer asset');
      return null;
    }
    return downloadUpdateAsset(
      downloadUrl: info.downloadUrl!,
      fileName: info.assetName!,
      onProgress: onProgress,
    );
  }

  static Future<String?> downloadUpdateAsset({
    required String downloadUrl,
    required String fileName,
    void Function(double progress)? onProgress,
  }) async {
    try {
      LogService.log(
          '⬇️ update.download start: file=$fileName, url=$downloadUrl');
      final tempDir = await getTemporaryDirectory();
      final sep = Platform.isWindows ? '\\' : '/';
      final filePath = '${tempDir.path}$sep$fileName';
      final request = http.Request('GET', Uri.parse(downloadUrl));
      request.headers.addAll(_headers);
      final response = await _client.send(request).timeout(
            const Duration(minutes: 3),
          );

      if (response.statusCode != 200) {
        LogService.log(
            '⚠️ update.download failed: status=${response.statusCode}, file=$fileName');
        return null;
      }

      final totalBytes = response.contentLength ?? 0;
      var received = 0;
      var nextProgressLog = 0.25;
      final file = File(filePath);
      final sink = file.openWrite();

      await for (final chunk in response.stream) {
        received += chunk.length;
        sink.add(chunk);
        if (totalBytes > 0 && onProgress != null) {
          final progress = (received / totalBytes).clamp(0.0, 1.0);
          onProgress(progress);
          if (progress >= nextProgressLog) {
            LogService.log(
                '⬇️ update.download progress: file=$fileName ${(progress * 100).toStringAsFixed(0)}% ($received/$totalBytes)');
            nextProgressLog += 0.25;
          }
        }
      }
      await sink.flush();
      await sink.close();

      if (!await file.exists() || await file.length() == 0) {
        LogService.log('⚠️ update.download failed: empty file for $fileName');
        return null;
      }

      onProgress?.call(1.0);
      LogService.log(
          '✅ update.download complete: path=$filePath, bytes=${await file.length()}');
      return filePath;
    } catch (e) {
      LogService.log('❌ update.download exception', error: e);
      return null;
    }
  }

  static Future<bool> installUpdate(String path) async {
    try {
      LogService.log('🚀 update.install start: path=$path');

      final file = File(path);
      if (!await file.exists()) {
        LogService.log('❌ update.install failed: file does not exist');
        return false;
      }

      // Android: open APK via system package installer
      if (Platform.isAndroid) {
        final result = await OpenFile.open(path);
        final ok = result.type == ResultType.done;
        LogService.log(ok
            ? '✅ update.install Android APK opened: $path'
            : '⚠️ update.install Android failed: ${result.message}');
        return ok;
      }

      final lower = path.toLowerCase();
      if (lower.endsWith('.msi')) {
        final result = await Process.run(
          'msiexec',
          ['/i', path, '/passive', '/norestart'],
          runInShell: false,
        );
        final ok = result.exitCode == 0 || result.exitCode == 3010;
        if (!ok) {
          LogService.log(
              '⚠️ update.install msi exit=${result.exitCode}, stderr=${result.stderr}');
        } else {
          LogService.log(
              '✅ update.install msi launched successfully: exit=${result.exitCode}');
        }
        return ok;
      }

      // Inno/EXE/MSIX
      await Process.start(path, [], runInShell: false);
      LogService.log('✅ update.install process started: $path');
      return true;
    } catch (e) {
      LogService.log('❌ update.install exception', error: e);
      return false;
    }
  }
}

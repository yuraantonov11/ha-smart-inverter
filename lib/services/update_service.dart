import 'dart:convert';
import 'dart:io';
import 'package:http/http.dart' as http;
import 'package:open_file/open_file.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:path_provider/path_provider.dart';
import 'package:url_launcher/url_launcher.dart';
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
  final int? assetSize;

  const UpdateInfo({
    required this.hasUpdate,
    required this.currentVersion,
    required this.latestVersion,
    this.releaseName,
    this.releaseNotes,
    this.publishedAt,
    this.assetName,
    this.downloadUrl,
    this.assetSize,
  });
}

class UpdateService {
  static const String releasesUrl =
      'https://api.github.com/repos/yuraantonov11/siseli-app/releases?per_page=20';
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
          .get(Uri.parse(releasesUrl), headers: _headers)
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

      final decoded = json.decode(response.body);
      final releases = (decoded is List ? decoded : const <dynamic>[])
          .whereType<Map<String, dynamic>>()
          .toList();

      final compatible = _findLatestCompatibleRelease(releases);
      if (compatible == null) {
        LogService.log(
            '⚠️ update.check: no compatible release asset found for platform=${Platform.operatingSystem}');
        return UpdateInfo(
          hasUpdate: false,
          currentVersion: currentVersion,
          latestVersion: currentVersion,
        );
      }

      final data = compatible.release;
      final asset = compatible.asset;
      final latestTag = (data['tag_name'] as String?) ?? currentVersion;
      final latestVersion = _cleanVersion(latestTag);
      final hasUpdate = _isVersionNewer(latestVersion, currentVersion);

      return UpdateInfo(
        hasUpdate: hasUpdate,
        currentVersion: currentVersion,
        latestVersion: latestVersion,
        releaseName: data['name']?.toString(),
        releaseNotes: data['body']?.toString(),
        publishedAt: DateTime.tryParse(data['published_at']?.toString() ?? ''),
        assetName: asset['name']?.toString(),
        downloadUrl: asset['browser_download_url']?.toString(),
        assetSize: _parseAssetSize(asset['size']),
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

  static ({Map<String, dynamic> release, Map<String, dynamic> asset})?
      _findLatestCompatibleRelease(List<Map<String, dynamic>> releases) {
    // GitHub releases API returns newest first; pick the first non-draft,
    // non-prerelease release that has an installer for current platform.
    for (final release in releases) {
      final isDraft = release['draft'] == true;
      final isPrerelease = release['prerelease'] == true;
      if (isDraft || isPrerelease) continue;

      final assets = (release['assets'] as List<dynamic>? ?? const [])
          .whereType<Map<String, dynamic>>()
          .toList();
      final asset = _pickBestInstallerAsset(assets);
      if (asset != null) {
        return (release: release, asset: asset);
      }
    }
    return null;
  }

  static int? _parseAssetSize(dynamic value) {
    if (value is int && value > 0) return value;
    if (value is String) {
      final parsed = int.tryParse(value);
      if (parsed != null && parsed > 0) return parsed;
    }
    return null;
  }

  static const int _maxDownloadAttempts = 2;

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
      expectedBytes: info.assetSize,
      onProgress: onProgress,
    );
  }

  static Future<String?> downloadUpdateAsset({
    required String downloadUrl,
    required String fileName,
    int? expectedBytes,
    void Function(double progress)? onProgress,
  }) async {
    final filePath = await _resolveDownloadPath(fileName);
    final file = File(filePath);

    if (await _canReuseDownloadedFile(
      file: file,
      fileName: fileName,
      expectedBytes: expectedBytes,
    )) {
      onProgress?.call(1.0);
      return filePath;
    }

    for (var attempt = 1; attempt <= _maxDownloadAttempts; attempt++) {
      IOSink? sink;
      try {
        LogService.log(
            '⬇️ update.download start: attempt=$attempt/$_maxDownloadAttempts, file=$fileName, url=$downloadUrl, path=$filePath');

        if (await file.exists()) {
          await file.delete();
        }

        final request = http.Request('GET', Uri.parse(downloadUrl));
        request.headers.addAll(_headers);
        final response = await _client.send(request).timeout(
              const Duration(minutes: 3),
            );

        if (response.statusCode != 200) {
          LogService.log(
              '⚠️ update.download failed: status=${response.statusCode}, file=$fileName, attempt=$attempt');
          if (attempt == _maxDownloadAttempts) return null;
          continue;
        }

        final totalBytes = response.contentLength ?? 0;
        var received = 0;
        var nextProgressLog = 0.25;
        sink = file.openWrite();

        await for (final chunk in response.stream) {
          received += chunk.length;
          sink.add(chunk);
          if (totalBytes > 0 && onProgress != null) {
            final progress = (received / totalBytes).clamp(0.0, 1.0);
            onProgress(progress);
            if (progress >= nextProgressLog) {
              LogService.log(
                  '⬇️ update.download progress: file=$fileName ${(progress * 100).toStringAsFixed(0)}% ($received/$totalBytes), attempt=$attempt');
              nextProgressLog += 0.25;
            }
          }
        }

        await sink.flush();
        await sink.close();
        sink = null;

        if (!await file.exists() || await file.length() == 0) {
          LogService.log(
              '⚠️ update.download failed: empty file for $fileName, attempt=$attempt');
          if (attempt == _maxDownloadAttempts) return null;
          continue;
        }

        onProgress?.call(1.0);
        LogService.log(
            '✅ update.download complete: path=$filePath, bytes=${await file.length()}');
        return filePath;
      } catch (e) {
        LogService.log('❌ update.download exception attempt=$attempt',
            error: e);
        if (attempt == _maxDownloadAttempts) {
          return null;
        }
      } finally {
        try {
          await sink?.flush();
          await sink?.close();
        } catch (_) {
          // Ignore cleanup issues on retry path.
        }
      }
    }

    return null;
  }

  static Future<bool> _canReuseDownloadedFile({
    required File file,
    required String fileName,
    required int? expectedBytes,
  }) async {
    if (!await file.exists()) return false;

    final existingBytes = await file.length();
    if (existingBytes <= 0) {
      try {
        await file.delete();
      } catch (_) {
        // Ignore cleanup failure and continue with fresh download.
      }
      return false;
    }

    if (expectedBytes != null && expectedBytes > 0) {
      if (existingBytes == expectedBytes) {
        LogService.log(
            'ℹ️ update.download reuse existing file: file=$fileName, bytes=$existingBytes');
        return true;
      }

      LogService.log(
          '⚠️ update.download existing file size mismatch: file=$fileName, local=$existingBytes, expected=$expectedBytes; redownloading');
      try {
        await file.delete();
      } catch (_) {
        // Best-effort cleanup before redownload.
      }
      return false;
    }

    LogService.log(
        'ℹ️ update.download reuse existing file without expected size: file=$fileName, bytes=$existingBytes');
    return true;
  }

  static Future<String> _resolveDownloadPath(String fileName) async {
    if (Platform.isAndroid) {
      try {
        // Keep APK in app-internal cache to avoid scoped-storage access issues
        // when Android Package Installer opens files from /Android/data paths.
        final tempDir = await getTemporaryDirectory();
        final updatesDir =
            Directory('${tempDir.path}${Platform.pathSeparator}updates');
        await updatesDir.create(recursive: true);
        return '${updatesDir.path}${Platform.pathSeparator}$fileName';
      } catch (_) {
        // Fallback below.
      }

      try {
        final appDir = await getApplicationSupportDirectory();
        final updatesDir =
            Directory('${appDir.path}${Platform.pathSeparator}updates');
        await updatesDir.create(recursive: true);
        return '${updatesDir.path}${Platform.pathSeparator}$fileName';
      } catch (_) {
        // Fallback below.
      }

      // Avoid external storage fallback on Android. APK installs are more
      // reliable when we stay in app-internal directories and hand off via
      // FileProvider/open_file.
      final appDir = await getApplicationDocumentsDirectory();
      final updatesDir =
          Directory('${appDir.path}${Platform.pathSeparator}updates');
      await updatesDir.create(recursive: true);
      return '${updatesDir.path}${Platform.pathSeparator}$fileName';
    }

    final tempDir = await getTemporaryDirectory();
    return '${tempDir.path}${Platform.pathSeparator}$fileName';
  }

  static Future<bool> installUpdate(String path, {String? fallbackUrl}) async {
    try {
      LogService.log('🚀 update.install start: path=$path');

      final file = File(path);
      if (!await file.exists()) {
        LogService.log('❌ update.install failed: file does not exist');
        return false;
      }

      // Android: open APK via system package installer
      if (Platform.isAndroid) {
        final result = await OpenFile.open(
          path,
          type: 'application/vnd.android.package-archive',
        );
        final ok = result.type == ResultType.done;
        if (ok) {
          LogService.log('✅ update.install Android APK opened: $path');
          return true;
        }

        LogService.log('⚠️ update.install Android failed: ${result.message}');

        if (fallbackUrl != null && fallbackUrl.isNotEmpty) {
          final uri = Uri.tryParse(fallbackUrl);
          if (uri != null) {
            final launched = await launchUrl(
              uri,
              mode: LaunchMode.externalApplication,
            );
            if (launched) {
              LogService.log(
                  'ℹ️ update.install fallback opened external URL: $fallbackUrl');
              return true;
            }
          }
        }

        return false;
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

import 'dart:convert';
import 'dart:io';
import 'package:http/http.dart' as http;
import 'package:package_info_plus/package_info_plus.dart';
import 'package:path_provider/path_provider.dart';
import 'log_service.dart';

class UpdateService {
  static const String repoUrl =
      'https://api.github.com/repos/yuraantonov11/siseli-app/releases/latest';

  static Future<bool> checkForUpdate() async {
    try {
      final response = await http.get(Uri.parse(repoUrl));
      if (response.statusCode == 200) {
        final data = json.decode(response.body);
        final latestTag = data['tag_name'] as String;
        final latestVersion =
            latestTag.startsWith('v') ? latestTag.substring(1) : latestTag;
        final latestClean = latestVersion.split('+')[0];

        final packageInfo = await PackageInfo.fromPlatform();
        final currentVersion = packageInfo.version.split('+')[0];

        // Simple version comparison (assumes semantic versioning)
        return _isVersionNewer(latestClean, currentVersion);
      }
    } catch (e) {
      LogService.log('Error checking for update', error: e);
    }
    return false;
  }

  static bool _isVersionNewer(String latest, String current) {
    final latestParts = latest.split('.').map(int.parse).toList();
    final currentParts = current.split('.').map(int.parse).toList();

    for (var i = 0; i < latestParts.length && i < currentParts.length; i++) {
      if (latestParts[i] > currentParts[i]) return true;
      if (latestParts[i] < currentParts[i]) return false;
    }
    return latestParts.length > currentParts.length;
  }

  static Future<String?> downloadUpdate(
      [void Function(double progress)? onProgress]) async {
    try {
      final response = await http.get(Uri.parse(repoUrl));
      if (response.statusCode == 200) {
        final data = json.decode(response.body);
        final assets = data['assets'] as List;
        final msiAsset = assets.firstWhere(
          (asset) => (asset['name'] as String).endsWith('.msi'),
          orElse: () => null,
        );
        if (msiAsset != null) {
          final downloadUrl = msiAsset['browser_download_url'];
          final tempDir = await getTemporaryDirectory();
          final filePath = '${tempDir.path}\\update.msi';

          final file = File(filePath);
          final downloadResponse = await http.get(Uri.parse(downloadUrl));
          if (downloadResponse.statusCode == 200) {
            await file.writeAsBytes(downloadResponse.bodyBytes);
            return filePath;
          }
        }
      }
    } catch (e) {
      LogService.log('Error downloading update', error: e);
    }
    return null;
  }

  static Future<bool> installUpdate(String path) async {
    try {
      final result =
          await Process.run('msiexec', ['/i', path, '/quiet', '/norestart']);
      return result.exitCode == 0;
    } catch (e) {
      LogService.log('Error installing update', error: e);
      return false;
    }
  }
}

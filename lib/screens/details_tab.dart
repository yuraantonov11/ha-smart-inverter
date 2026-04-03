import 'package:flutter/material.dart';

import '../models/inverter_data.dart';

class DetailsTab extends StatelessWidget {
  final InverterData data;

  const DetailsTab({super.key, required this.data});

  @override
  Widget build(BuildContext context) {
    final fields = data.rawFields;
    final keys = fields.keys.toList()..sort();

    return ListView.builder(
      padding: const EdgeInsets.all(16),
      itemCount: keys.length,
      itemBuilder: (context, index) {
        final key = keys[index];
        final fieldData = fields[key];

        var name = key;
        var val = 'N/A';
        var unit = '';

        if (fieldData is Map) {
          name = fieldData['nameDisplay'] ?? key;
          val = fieldData['valueDisplay']?.toString() ??
              fieldData['value']?.toString() ??
              'N/A';
          unit = fieldData['unit']?.toString() ?? '';
        } else {
          val = fieldData.toString();
        }

        return Card(
          color: const Color(0xFF1E293B),
          margin: const EdgeInsets.only(bottom: 8),
          child: ListTile(
            title: Text(name,
                style: const TextStyle(fontSize: 14, color: Colors.white70)),
            trailing: Text('$val $unit'.trim(),
                style: const TextStyle(
                    fontSize: 16,
                    fontWeight: FontWeight.bold,
                    color: Colors.amber)),
          ),
        );
      },
    );
  }
}

import 'package:flutter_test/flutter_test.dart';
import 'package:smart_inverter/main.dart';

void main() {
  testWidgets('App starts with 0', (WidgetTester tester) async {
    await tester.pumpWidget(const InverterApp());
    expect(find.text('0'), findsOneWidget);
  });
}

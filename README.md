# Smart Inverter Desktop ⚡️

A modern, fast, and elegant desktop application built with Flutter for monitoring and controlling solar inverters integrated with `solar.siseli.com` using the Wi-Fi module ECO/MAX-730.

## Features 🚀
- **Real-time Monitoring**: Watch your solar, grid, battery, and home load metrics with animated energy flow diagrams.
- **Instant Control**: Switch between SBU (Solar Priority) and USB (Utility Priority) with a single click.
- **Advanced Settings**: Change output and charger priorities remotely (CSO, SNU, OSO).
- **Automation**: Setup schedules (e.g., charge from grid during cheap night tariffs) and weather-based automations.
- **System Tray Integration**: Run silently in the background. See battery status on hover and switch modes directly from the Windows taskbar.
- **Multi-language**: Seamlessly switch between English and Ukrainian.

## Technology Stack 💻
- **Flutter** & **Dart**
- **Provider** for state management
- **Dio** for fast HTTP requests
- **Encrypt & Crypto** for secure SmartESS API signature generation

## Installation 📥
Download the latest version from the [Releases](https://github.com/yuraantonov11/smart_inverter/releases) page. Currently supports Windows (x64).

## Versioning 📌
We use [SemVer](http://semver.org/) for versioning. For the versions available, see the [tags on this repository](https://github.com/yuraantonov11/smart_inverter/tags).

## Contributing 🤝
Please read [CONTRIBUTING.md](CONTRIBUTING.md) for details on our code of conduct, and the process for submitting pull requests to us.

## License 📄
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Disclaimer ⚠️
This application uses the internal API of the solar.siseli.com platform. Use at your own risk.
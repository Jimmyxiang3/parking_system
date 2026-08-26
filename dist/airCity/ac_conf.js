/**
 * AirCity 配置文件
 *
 * 注意: 此文件在 index.html 中以 <script> 方式加载，无法使用 ES import。
 * 配置值通过全局 HostConfig 变量注入。
 * 实际部署时请修改此文件中的地址。
 *
 * Manager:  Cloud管理服务的地址
 * Player:   Cloud连接视频流和API调用的地址
 * Path:     SDK文件夹所在的绝对路径
 */
var HostConfig = {
  Manager: "192.168.5.197:30005",
  Player: "192.168.5.197:30005",
  PlayerMapping: "58.250.250.180:30005",
  Path: "C:\\Users\\Admin\\AppData\\Roaming\\AirCityCloud\\SDK",
  NeedLogin: false,
};

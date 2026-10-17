using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using LibreHardwareMonitor.Hardware;

internal static class SensorHelper
{
    private static string JsonString(string value)
    {
        if (value == null) return "null";
        return "\"" + value.Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
    }

    private static string JsonNumber(float? value)
    {
        return value.HasValue ? value.Value.ToString("0.###", CultureInfo.InvariantCulture) : "null";
    }

    private static float? Valid(ISensor sensor, float min, float max)
    {
        if (!sensor.Value.HasValue || sensor.Value.Value <= min || sensor.Value.Value >= max) return null;
        return sensor.Value.Value;
    }

    public static int Main(string[] args)
    {
        string cpuName = null;
        string gpuName = null;
        float? cpuTemp = null, cpuVoltage = null, cpuClock = null, cpuPower = null;
        float? gpuTemp = null, gpuVoltage = null, gpuClock = null, gpuPower = null;
        var computer = new Computer
        {
            IsCpuEnabled = true,
            IsGpuEnabled = true,
            IsMotherboardEnabled = true
        };

        try
        {
            computer.Open();
            foreach (IHardware hardware in computer.Hardware)
            {
                hardware.Update();
                foreach (IHardware child in hardware.SubHardware) child.Update();
            }
            Thread.Sleep(250);
            foreach (IHardware hardware in computer.Hardware)
            {
                hardware.Update();
                if (hardware.HardwareType == HardwareType.Cpu)
                {
                    cpuName = hardware.Name;
                    var temperatures = hardware.Sensors.Where(s => s.SensorType == SensorType.Temperature).ToList();
                    ISensor preferredTemp = temperatures.FirstOrDefault(s => s.Name.IndexOf("Tctl/Tdie", StringComparison.OrdinalIgnoreCase) >= 0)
                        ?? temperatures.FirstOrDefault(s => s.Name.IndexOf("Package", StringComparison.OrdinalIgnoreCase) >= 0)
                        ?? temperatures.FirstOrDefault();
                    if (preferredTemp != null) cpuTemp = Valid(preferredTemp, 0, 130);

                    var vids = hardware.Sensors
                        .Where(s => s.SensorType == SensorType.Voltage && s.Name.IndexOf("VID", StringComparison.OrdinalIgnoreCase) >= 0)
                        .Select(s => Valid(s, 0, 2)).Where(v => v.HasValue).Select(v => v.Value).ToList();
                    if (vids.Count > 0) cpuVoltage = vids.Average();

                    var clocks = hardware.Sensors.Where(s => s.SensorType == SensorType.Clock).ToList();
                    ISensor averageClock = clocks.FirstOrDefault(s => s.Name.Equals("Cores (Average)", StringComparison.OrdinalIgnoreCase));
                    if (averageClock != null) cpuClock = Valid(averageClock, 0, 10000);
                    if (!cpuClock.HasValue)
                    {
                        var coreClocks = clocks.Where(s => s.Name.StartsWith("Core #") && s.Name.IndexOf("Effective", StringComparison.OrdinalIgnoreCase) < 0)
                            .Select(s => Valid(s, 0, 10000)).Where(v => v.HasValue).Select(v => v.Value).ToList();
                        if (coreClocks.Count > 0) cpuClock = coreClocks.Average();
                    }

                    ISensor power = hardware.Sensors
                        .Where(s => s.SensorType == SensorType.Power)
                        .OrderByDescending(s => s.Name.IndexOf("Package", StringComparison.OrdinalIgnoreCase) >= 0)
                        .FirstOrDefault();
                    if (power != null) cpuPower = Valid(power, 0, 1000);
                }
                else if (hardware.HardwareType == HardwareType.GpuNvidia)
                {
                    gpuName = hardware.Name;
                    ISensor sensor = hardware.Sensors.FirstOrDefault(s => s.SensorType == SensorType.Temperature && s.Name.Equals("GPU Core", StringComparison.OrdinalIgnoreCase));
                    if (sensor != null) gpuTemp = Valid(sensor, 0, 130);
                    sensor = hardware.Sensors.FirstOrDefault(s => s.SensorType == SensorType.Voltage && s.Name.IndexOf("Core Voltage", StringComparison.OrdinalIgnoreCase) >= 0);
                    if (sensor != null) gpuVoltage = Valid(sensor, 0, 2);
                    sensor = hardware.Sensors.FirstOrDefault(s => s.SensorType == SensorType.Clock && s.Name.Equals("GPU Core", StringComparison.OrdinalIgnoreCase));
                    if (sensor != null) gpuClock = Valid(sensor, 0, 10000);
                    sensor = hardware.Sensors
                        .Where(s => s.SensorType == SensorType.Power)
                        .OrderByDescending(s => s.Name.IndexOf("GPU", StringComparison.OrdinalIgnoreCase) >= 0 ||
                                                s.Name.IndexOf("Total", StringComparison.OrdinalIgnoreCase) >= 0)
                        .FirstOrDefault();
                    if (sensor != null) gpuPower = Valid(sensor, 0, 2000);
                }

                // Ryzen temperature can also be exposed by the board Super I/O.
                if (!cpuTemp.HasValue && (hardware.HardwareType == HardwareType.Motherboard || hardware.HardwareType == HardwareType.SuperIO))
                {
                    var boardSensors = hardware.Sensors.Concat(hardware.SubHardware.SelectMany(h => h.Sensors));
                    ISensor sensor = boardSensors.FirstOrDefault(s => s.SensorType == SensorType.Temperature &&
                        (s.Name.Equals("CPU", StringComparison.OrdinalIgnoreCase) ||
                         s.Name.IndexOf("CPU Package", StringComparison.OrdinalIgnoreCase) >= 0 ||
                         s.Name.IndexOf("CPU Socket", StringComparison.OrdinalIgnoreCase) >= 0));
                    if (sensor != null) cpuTemp = Valid(sensor, 0, 130);
                }
            }
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine(ex.Message);
            return 1;
        }
        finally
        {
            computer.Close();
        }

        string json = "{" +
            "\"cpu_name\":" + JsonString(cpuName) + "," +
            "\"cpu_temp_c\":" + JsonNumber(cpuTemp) + "," +
            "\"cpu_voltage_v\":" + JsonNumber(cpuVoltage) + "," +
            "\"cpu_freq_mhz\":" + JsonNumber(cpuClock) + "," +
            "\"cpu_power_w\":" + JsonNumber(cpuPower) + "," +
            "\"gpu_name\":" + JsonString(gpuName) + "," +
            "\"gpu_temp_c\":" + JsonNumber(gpuTemp) + "," +
            "\"gpu_voltage_v\":" + JsonNumber(gpuVoltage) + "," +
            "\"gpu_freq_mhz\":" + JsonNumber(gpuClock) + "," +
            "\"gpu_power_w\":" + JsonNumber(gpuPower) + "}";
        if (args.Length > 0) File.WriteAllText(args[0], json, new UTF8Encoding(false));
        else Console.WriteLine(json);
        return 0;
    }
}

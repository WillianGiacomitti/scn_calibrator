import serial

printer_serial = serial.Serial('COM3', 115200)

command_list = ['G1 X100 Y100', 'G1 X100 Y200', 'G1 X200 Y200', 'G1 X200 Y100', 'G1 X100 Y100', 'G1 Z50' ]

def send_cmd(command):
    command = command + "\n"
    printer_serial.write(command.encode())

    response = printer_serial.readline().decode().strip()
    print(response)

    while response.lower() != 'ok':
        response = printer_serial.readline().decode().strip()
        print(response)


input("press enter to  continue")

# home = 'G28' + "\n"
# send_cmd(home)

for command in command_list:
    send_cmd(command=command)


while True:
    inpur_user = input("Enter command: ")
    send_cmd(inpur_user)
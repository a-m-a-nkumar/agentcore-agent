import re
import os

file_path = "c:/Users/T479888/.aws/credentials"
with open(file_path, "r") as f:
    text = f.read()

new_section = """[590184044598_PowerUser]
aws_access_key_id=ASIAYS2NV3A3FJYPPG7R
aws_secret_access_key=KE+0xZ8s6sfIRN2DjGNgVwswRqlRbsBXbbKmjKUQ
aws_session_token=IQoJb3JpZ2luX2VjEND//////////wEaCXVzLWVhc3QtMSJHMEUCIG/3Z3VRAhhxPeJkGmEr+pxo0V7EeCiwlyKjam0iz+d+AiEAlJBdtHAIPrLRmyzgU+5y3Ih1R9VFdoIxlrrytjJjzHQqjgMImf//////////ARAAGgw1OTAxODQwNDQ1OTgiDBm8Ic+A9dQlb/LxjyriAt+fC2q6juQqMRZeH5Y6rAW+5BJKdlmTNeRf9LVPT9y8sL4yIELeeio5lsKQrtA51XlLAj+9rjD5k4HnHJI0YvUgl66oeqx7u/MDaTKOM7VSkR+A/PzPxIZoW5OeY8pab9PSgCBQZbYHs1OVItYcvQrGckS0WFHw4uVTLXcN2btC8XRPPYPTHt+aVLneau/dRGH1abQk7WfOcVvNKyCG9VXDVIGLqyms1hlPqH4FM+/GNR2ecZqNl4F3iR8n9qh5Lc1fNitE87PLGycH36tgC8Ffg8zzke0s45eHXevuJgjWRLTXi+hvElwHiqN3jFJ2d51yAAnjNLAV81mjnjU7XWiYY/1Cs7r2BlyPhdGn+imiXRzwWnOnvPUvjalszNH3kWbM4BMaGAOeppQJBxCylH9uVmcbpyxpoPoPm8c9eKlKGxK/ADFDSGnP2VxX3b8F12AKEFAOMGI1i8Lev7OPdQvDoDCdqprNBjqkAaqYewukCuzWFeE8Lh3zeKV/RRwNQIOnxOo4mc1c5coHpaQiCtSbfiurU6kFQpDMPlYQd+WGwlh1kktOsKsZaPzXf6OUdqP6GkIHFcXUeuFEvQR6sVJ0FrtnZR/2uuQBzjatc/qG48NyMSujFGHyjbB9cIY3O1PH/qa1SbJ7b+F8LF06w3VZkN4hEUXH0BJ30OhopS44yo/MqHGLyDRjAlXKJQMv"""

if "[590184044598_PowerUser]" in text:
    text = re.sub(r'\[590184044598_PowerUser\].*', new_section, text, flags=re.DOTALL)
else:
    if not text.endswith("\n"):
        text += "\n"
    text += new_section

with open(file_path, "w") as f:
    f.write(text)

print("Updated credentials OK")

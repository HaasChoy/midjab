#question2 - armstrong number
def armstrong_n(n: int) -> bool:  
  s = str(n)
  power = len(n)
  
  total = sum(int(i) ** power for i in s)
  return total == n 


num = int(input("Enter a number: "))
if armstrong_n(num):
  print("It is an armstrong number")
else:
  print("It is not an armstrong number")
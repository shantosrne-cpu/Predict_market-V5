from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import declarative_base

# Base para definir los modelos
Base = declarative_base ()


# Ejemplo de tabla
class ExampleTable (Base):
    __tablename__ = "example_table"

    id = Column (Integer, primary_key=True, index=True)  # Columna primaria
    name = Column (String, nullable=False)  # Nombre (no nulo)

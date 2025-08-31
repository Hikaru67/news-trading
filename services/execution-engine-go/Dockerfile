# Build stage
FROM golang:1.21-alpine AS builder

WORKDIR /app

# Install git, gcc and build dependencies
RUN apk add --no-cache git gcc musl-dev

# Copy go mod files
COPY services/execution-engine-go/go.mod ./

# Download dependencies and create go.sum
RUN go mod download && go mod tidy && go mod verify

# Copy source code
COPY services/execution-engine-go/ .

# Get dependencies and build
RUN go get . && CGO_ENABLED=1 GOOS=linux go build -a -installsuffix cgo -o main .

# Final stage
FROM alpine:latest

# Install ca-certificates for HTTPS requests
RUN apk --no-cache add ca-certificates

WORKDIR /root/

# Copy the binary from builder stage
COPY --from=builder /app/main .

# Create logs directory
RUN mkdir -p /root/logs

# Expose port (if needed for metrics)
EXPOSE 8001

# Run the binary
CMD ["./main"]
